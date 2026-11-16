"""Q1 v1: deterministic geometric proxies, not aesthetic or contest scores."""
import math
import re
from PIL import Image, ImageChops, ImageDraw, ImageFilter
from app.engine.sample import sample_scene
from app.schemas.ir import DrawingIR
from app.schemas.quality import Assessment, Evidence, Metric, Recommendation

VERSION = 'codepoet-quality-1.0.0'
THRESHOLDS = dict(overlapRatio=0.12, paletteDistance=0.12, noiseRatio=0.02,
                  maskWidth=200, maskHeight=150, maskDilationCells=1, foregroundPixelThreshold=8, maxColorCombinations=60000)
NAMES = ('parameterValidity', 'layoutConflict', 'colorSemantic', 'signalNoise')
LIMITATIONS = [
    '200×150离散mask与一格边缘容差是几何代理，细节可能漏检；不是审美评分。',
    '交叠以顶层子树为独立主体，同组内设计交叠不计入；可能漏报组内冲突。',
    '色彩只评估有历史色板依据的填充可见区域，排除描边及抗锯齿边缘；不评价色彩美感。',
    'SNR是PNG前景与IR几何的一致性代理，不代表赛题得分或可信执行证明；少量边缘杂点可能被容差忽略。',
    '阈值为v1启发式，score未校准为null；12标注样本的命中率不代表真实作品泛化。',
    '最多分析60000种透明度/像素色组合，超过时色彩指标为unavailable；粗描边接角和半透明混色仍是离散代理。',
]


def unavailable(times: list[int]) -> Assessment:
    return Assessment(algorithmVersion=VERSION, status='unavailable', evaluatedTimesMs=times,
        thresholds=THRESHOLDS, limitations=LIMITATIONS,
        metrics=[Metric(name=name, status='unavailable', method='quality-assessment-unavailable',
                        evidence=[Evidence(message='质量分析暂不可用，已保留成功的PNG和基础检查。')]) for name in NAMES])


def _rgb(color: str) -> tuple[int, int, int]:
    return tuple(int(color[i:i+2], 16) for i in (1, 3, 5))


def _palette(source: dict) -> list[str]:
    decision = source.get('paletteDecision') or {}
    values = decision.get('value')
    if isinstance(values, str):
        values = [item.strip() for item in values.split(',')]
    if not isinstance(values, list):
        values = [color for rule in source.get('matchedRules', [])
                  for color in (rule.get('content', {}).get('effects', {}).get('palette') or [])]
    return list(dict.fromkeys(color.upper() for color in values
                if isinstance(color, str) and re.fullmatch(r'#[0-9a-fA-F]{6}', color)))[:16]


def _mask(node, viewport, *, fill=True, stroke=True):
    mask = Image.new('1', (200, 150))
    if node.opacity <= 0:
        return mask
    points = [((x-viewport['x'])*200/viewport['width'], (y-viewport['y'])*150/viewport['height'])
              for x, y in node.points]
    draw = ImageDraw.Draw(mask)
    if fill and node.style.get('fill') is not None:
        # Canvas fills an open polyline by implicitly closing its path as well.
        draw.polygon(points, fill=1)
    if stroke and node.style.get('stroke') is not None and node.style.get('strokeWidth', 1) > 0:
        width = max(1, round(node.style.get('strokeWidth', 1)*max(200/viewport['width'], 150/viewport['height'])))
        outline = points if node.type == 'polyline' else points + points[:1]
        draw.line(outline, fill=1, width=width, joint='curve')
    return mask


def _bits(mask):
    return int.from_bytes(mask.tobytes(), 'big')


def _foreground(image, background):
    base = Image.new('RGBA', image.size, (*_rgb(background), 255))
    rendered = Image.alpha_composite(base, image.convert('RGBA')).convert('RGB')
    difference = ImageChops.difference(rendered, base.convert('RGB'))
    r, g, b = difference.split()
    peak = ImageChops.lighter(ImageChops.lighter(r, g), b)
    return rendered, peak.point(lambda value: 255 if value > 8 else 0).convert('1')


def assess(ir: DrawingIR, images: dict[int, Image.Image], source: dict, validation: dict) -> Assessment:
    metrics = {}
    recommendations = []
    parameter_ok = validation.get('parameterValid')
    issues = validation.get('issues') or []
    bounds = validation.get('outOfBoundsNodeCount', 0)
    status = 'unavailable' if parameter_ok is None else 'fail' if not parameter_ok else 'warn' if issues or bounds else 'pass'
    metrics['parameterValidity'] = Metric(name='parameterValidity', status=status,
        value=None if parameter_ok is None else float(bool(parameter_ok)),
        method='stored-generation-validation-v1; 参数与结构合法不代表像素或审美通过。',
        evidence=[Evidence(message=str(issue.get('message', '生成校验提示'))[:500],
                           nodeIds=issue.get('nodeIds', [])[:200]) for issue in issues[:25]])
    allowed = _palette(source)
    palette = [_rgb(color) for color in allowed]
    by_id = {node.id: node for node in ir.nodes}

    def root(node_id):
        current = by_id[node_id]
        while current.parentId is not None:
            current = by_id[current.parentId]
        return current.id

    overlap_evidence, color_evidence, signal_evidence = [], [], []
    worst_overlap, weighted_distance, color_area, worst_noise = 0.0, 0.0, 0, 0.0
    minimum_snr, empty = None, False
    color_distances, color_available = {}, True
    color_budget = THRESHOLDS['maxColorCombinations']
    evaluated = []
    for time, image in sorted(images.items()):
        scene = sample_scene(ir, time)
        evaluated.append(time)
        total = Image.new('1', (200, 150))
        subjects = {}
        leaf_masks = []
        stroke_mask = Image.new('1', (200, 150))
        for node in scene.nodes:
            mask = _mask(node, scene.viewport)
            total = ImageChops.logical_or(total, mask)
            key = root(node.id)
            subjects[key] = ImageChops.logical_or(subjects[key], mask) if key in subjects else mask
            leaf_masks.append((node, _mask(node, scene.viewport, stroke=False), mask))
            stroke_mask = ImageChops.logical_or(stroke_mask, _mask(node, scene.viewport, fill=False))
        candidates = [(key, mask.getbbox(), _bits(mask)) for key, mask in subjects.items() if mask.getbbox()]
        for index, (first, a, bits_a) in enumerate(candidates):
            for second, b, bits_b in candidates[index+1:]:
                if a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1]:
                    continue
                ratio = (bits_a & bits_b).bit_count() / min(bits_a.bit_count(), bits_b.bit_count())
                worst_overlap = max(worst_overlap, ratio)
                if ratio > THRESHOLDS['overlapRatio'] and len(overlap_evidence) < 25:
                    overlap_evidence.append(Evidence(message='独立顶层主体交叠，请检查是否符合构图意图。',
                        nodeIds=[first, second], timeMs=time, value=round(ratio, 6)))
        rendered, foreground = _foreground(image, ir.canvas.background)
        tolerance = total.convert('L').filter(ImageFilter.MaxFilter(3)).convert('1').resize(image.size, Image.Resampling.NEAREST)
        observed = _bits(foreground)
        expected = _bits(tolerance)
        signal, noise = (observed & expected).bit_count(), (observed & ~expected).bit_count()
        is_empty = observed.bit_count() / (image.width * image.height) < 0.001
        empty = empty or is_empty
        snr = 10 * math.log10((signal + 1) / (noise + 1))
        minimum_snr = min(minimum_snr, snr) if minimum_snr is not None else snr
        ratio = noise / max(1, signal + noise)
        worst_noise = max(worst_noise, ratio)
        signal_evidence.append(Evidence(message='空白输出，不能用高SNR判为良好。' if is_empty else 'IR几何内/外的实际PNG前景计数。',
            timeMs=time, value=round(snr, 4), details={'signalPixels':signal, 'noisePixels':noise, 'noiseRatio':round(ratio,6)}))
        if palette:
            # Exclude expected stroke and AA boundaries. Reverse draw order removes
            # fills hidden by opaque later leaves; translucent mixtures remain a proxy.
            covered = 0
            excluded = _bits(stroke_mask.convert('L').filter(ImageFilter.MaxFilter(3)).convert('1'))
            pixels = rendered.resize((200,150), Image.Resampling.NEAREST)
            black = Image.new('RGB',(200,150))
            for node, fill, whole in reversed(leaf_masks):
                visible = _bits(fill.convert('L').filter(ImageFilter.MinFilter(3)).convert('1')) & ~covered & ~excluded
                count, distance = visible.bit_count(), 0.0
                background = _rgb(ir.canvas.background)
                effective_palette = [tuple(round(channel*node.opacity + background[index]*(1-node.opacity))
                                           for index, channel in enumerate(color)) for color in palette]
                if count and color_available:
                    selection=Image.frombytes('1',(200,150),visible.to_bytes(3750,'big'))
                    histogram=Image.composite(pixels,black,selection).getcolors(30001)
                    for amount,rgb in histogram:
                        if rgb == (0,0,0):
                            amount -= 30000-count
                        if amount <= 0:
                            continue
                        key=(node.opacity,rgb)
                        if key not in color_distances:
                            if color_budget <= 0:
                                color_available=False
                                break
                            color_budget-=1
                            color_distances[key]=min(math.dist(rgb,color)/math.sqrt(3*255**2) for color in effective_palette)
                        distance+=amount*color_distances[key]
                if count:
                    weighted_distance += distance
                    color_area += count
                    if distance/count > THRESHOLDS['paletteDistance'] and len(color_evidence) < 25:
                        color_evidence.append(Evidence(message='可见填充与历史允许色板距离较大；描边已排除。',
                            nodeIds=[node.id], timeMs=time, value=round(distance/count,6), details={'maskPixels':count}))
                if node.opacity >= 0.999:
                    covered |= _bits(whole)

    distance = weighted_distance / color_area if color_area and color_available else None
    metrics['layoutConflict'] = Metric(name='layoutConflict', status='warn' if worst_overlap > THRESHOLDS['overlapRatio'] else 'pass',
        value=round(worst_overlap,6), method='top-level-subtree-mask-intersection/min-area-v1; 200×150，AABB筛选，同组交叠不计，透明度只作可见性筛选。', evidence=overlap_evidence)
    metrics['colorSemantic'] = Metric(name='colorSemantic', status='unavailable' if not color_available else 'not_applicable' if not allowed or not color_area else 'warn' if distance > THRESHOLDS['paletteDistance'] else 'pass',
        value=round(distance,6) if distance is not None else None,
        method='visible-fill-palette-RGB-distance-v1; 历史色板，PNG可见填充面积加权；排除描边与边缘，不透明遮挡按画序剔除，半透明混色为代理。',
        evidence=[Evidence(message='可见颜色组合超过本版有界分析容量，色彩指标不可用。')] if not color_available else color_evidence or [Evidence(message='没有历史色板依据或没有可评估的可见填充。' if not allowed or not color_area else '允许色板来自保存时来源快照。', details={'palette':','.join(allowed)})])
    metrics['signalNoise'] = Metric(name='signalNoise', status='fail' if empty else 'warn' if worst_noise > THRESHOLDS['noiseRatio'] else 'pass',
        value=round(minimum_snr,4) if minimum_snr is not None else None,
        method='geometry-PNG-proxy-SNR-v1; 10log10((S+1)/(N+1))，阈值8，200×150几何mask膨胀1格，空白单独失败。', evidence=signal_evidence)
    if not evaluated:
        return unavailable([])
    for metric in metrics.values():
        if metric.status in ('warn', 'fail'):
            evidence = metric.evidence or [Evidence(message='检查生成参数与视口。')]
            for item in evidence:
                if len(recommendations) >= 20:
                    break
                recommendations.append(Recommendation(code={'parameterValidity':'CHECK_PARAMETERS', 'layoutConflict':'SEPARATE_SUBJECTS',
                    'colorSemantic':'CHECK_PALETTE', 'signalNoise':'CHECK_OUTPUT'}[metric.name], message=item.message,
                    nodeIds=item.nodeIds, timeMs=item.timeMs))
    statuses = [metric.status for metric in metrics.values()]
    overall = 'fail' if 'fail' in statuses else 'warn' if 'warn' in statuses else 'unavailable' if 'unavailable' in statuses else 'pass'
    return Assessment(algorithmVersion=VERSION, status=overall, evaluatedTimesMs=evaluated,
        thresholds=THRESHOLDS, metrics=list(metrics.values()), recommendations=recommendations, limitations=LIMITATIONS)
