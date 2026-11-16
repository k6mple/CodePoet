# 交接：任务四 Q1 质量治理真实闭环

日期：2026-11-13（Asia/Shanghai）

任务：Q1-B，以及用户明确授权补齐的Q1-A2。基线16beb6f，分支`feat/q1b-quality-20261113`；本记录随Q1实现提交，提交/推送已另获授权，未改写已推送的任务2/3或合并PR。任务2/3已提交16beb6f并推送，[草稿PR #23](https://github.com/k6mple/CodePoet/pull/23)暂以#20分支为base。

## 接手者需要知道的变化

- 接收[PR #21](https://github.com/k6mple/CodePoet/pull/21)的Python独立采样、测试与数值fixture（来源ba5435a），没有合并其Git提交。重新用当前TS真实模块图在无DOM环境产生9组/16时刻期望值，与Pythonfixture完全一致。负缩放仍不属于现行schema，不把180度旋转声称为镜像覆盖。生成脚本改为跨平台加载真实TS模块，消除Windows tsc入口和any替身问题。
- `QualityReport`保留基础字段，新增可选`assessment`，版本`codepoet-quality-1.0.0`。服务端收齐上传PNG后读取存储的IR、来源、validationReport独立计算，保存在既有quality_report_json；**无新增迁移、不回算或重写历史报告**。capabilities.features.qualityAssessment=true；前端缺字段按未启用/未评估兼容。
- 四指标：参数有效性、独立主体交叠、色彩依据、信号/杂散。包含status/value/method/evidence，score全部null，没有综合分或赛题分。交叠为200×150顶层子树mask+AABB，阈值0.12；同组交叠不算冲突。色彩依据是历史paletteDecision或规则快照，阈值0.12，排除描边/边缘、按可见填充面积加权；没有依据为not_applicable。SNR为10log10((S+1)/(N+1))，实际前景阈值8、几何mask膨胀1格，杂散比例阈值0.02，空白独立fail。
- 有界分析：证据最多100条、建议20条、时刻最多3个；颜色/透明度组合最多60000种，超限仅该项unavailable。分析异常或有帧不能读取时报告unavailable，已经确认的PNG及成功上传状态保留，不伪造好分数、不返回内部异常文字。
- 真实联调发现手工IR和子图派生原先清空色板/规则来源，已统一继承历史快照并验证；不重新查当前规则、不由客户端提供色板评分，不修改旧generation。子图仍记录自身编辑版本和片段来源。
- 前端将“生成校验”和“保存后的像素质量”分开；保存前明确尚未评估，旧报告仅显示基础检查。支持算法版本、时刻、单位、阈值、适用范围、测量依据与建议。定位暂停到目标时刻并高亮节点（组包含叶节点）；定位框独立于renderer，**不进入PNG**。
- “准备修改”需确认后打开IR编辑，用户检查/修改并重新生成、保存新版本；不自动改图，不把原报告套到未应用的草稿上。历史页可定位并转到原作品的新版本编辑，取消不写入；换作品清空旧定位状态。

## 证据与验证边界

标注集在[shared/demo/quality](../../shared/demo/quality/README.md)：12例、6坏例、6个坏例改进IR，24张真实Chrome Canvas PNG与hash。覆盖正常分离/同组交叠/TRS裁剪/细线/透明/动画，以及独立主体交叠、违规色、真实PNG杂点、空白、出界、中间时刻碰撞。样本标签独立给出理由；PNG并非HTTP响应fixture。

必要脱敏结果在[本记录证据目录](2026-11-13-q1-quality-integration)：真实HTTP/UI结果、原/改进后报告与杂点报告。原始截图、完整测试与服务日志不共享。以下均在本机Windows、Chrome headless、DPR=1、Next生产构建、真实FastAPI/SQLite隔离部署执行。

| 检查 | 结果 |
| --- | --- |
| typecheck / 最终production build | 通过；API_PROXY_TARGET与测试后端一致 |
| 后端全量 `python -m unittest discover -s backend/tests -q` | 357项OK，退出0；仍有旧测试临时SQLite finalizer的WinError32清理告警，不宣称无告警 |
| 标注和真实HTTP上传 | 12例、6坏例和6个改进版本，四指标预期全部匹配；这12例上误报0/6、漏报0/6，**不是泛化准确率** |
| 真实质量闭环 | 4组流程通过：保存前未评估；12例+6改进及旧报告不变；2000ms/节点定位→确认→手工修改→新generation→新作品版本；新PNG hash与无定位框的规范PNG相同 |
| 旧/不可用报告组件 | 2项通过，使用改写响应的独立组件兼容检查；**不作为真实后端算法/故障验收**。真实故障回退、缺帧、历史读/不回算另有后端HTTP测试 |
| 移动端 | 390px无水平溢出，桌面/移动截图本地查看；不是多设备全面验收 |
| Canvas回归 | 46帧与旧实现逐像素及编码一致，12组采样/协议检查通过 |
| 现有真实链路 | 默认Canvas G0 20、P1 13、CSS 7项通过；fixture控件17项通过，无pageErrors |
| 分析性能/上限 | 200个重叠半透明主体单帧实测约0.27秒；证据51、建议20。随机高颜色组合超预算明确unavailable，未宣称所有输入/机器均达到此耗时 |

复跑前准备独立数据库/seed与环境凭据，所有输出目录显式设到本地日志，不使用脚本历史共享默认路径。Q1脚本环境变量为`CODEPOET_Q1_BASE_URL`、`CODEPOET_Q1_OUTPUT_DIR`、`CODEPOET_ACCEPTANCE_PASSWORD`；密钥不写入文件。脚本会真实创建私有样例作品和新版本，不应指向公共部署。

```text
node scripts/gen_sample_expected.mjs --out <local-output-json>
node frontend/tests/quality-samples-render.mjs <local-png-output>
python -m unittest test_sample test_assessment test_quality
node frontend/tests/quality-api-check.mjs
```

## 待办与责任方

- A/B合入：Q1包含#21必要采样文件，合并相关PR时核对同路径差异；本卡不抢新增迁移号。任务2/3的#23仍依赖#20，整合顺序需要保留。
- 部署负责人：前后端同批发布，目标环境重新验证PNG、报告与编辑保存；本轮未部署公共环境。旧报告继续可读，缺assessment不能自动宣称已评估。
- 算法后续：固定阈值和12标注例仅是初始校准。同组缺陷、极端描边接角、细节mask量化、透明混色和低于边缘容差的噪点都有漏报/误报可能；真实复杂文化图案尚需独立数据验证，不能调标签包装成普适审美结论。
- 本轮未实现WebGL、Turtle/server worker、DSL、K1或LLM，不因Q1通过扩大这些能力的声明。
