// Static checks for shared/demo content. This is not a substitute for the backend
// validator or a real import; it catches broken references and invalid IR locally.
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..')
const read = (relative) => JSON.parse(fs.readFileSync(path.join(root, relative), 'utf8'))
const concepts = read('shared/demo/knowledge/concepts.json')
const tags = read('shared/demo/knowledge/tags.json')
const knowledge = read('shared/demo/knowledge/knowledge.json')
const lotus = read('shared/demo/templates/lotus.json')
const bamboo = read('shared/demo/templates/bamboo.json')
const narrative = read('shared/demo/templates/narrative.json')
const rules = read('shared/demo/rules/rules.json')
const fixtures = [
  read('shared/demo/fixtures/basic_shapes.json'),
  read('shared/demo/fixtures/canvas_probe.json'),
  read('shared/demo/fixtures/animation_sequence.json'),
  read('shared/demo/fixtures/narrative_sequence.json'),
  read('shared/demo/fixtures/parent_child_crop.json'),
]

const errors = []
const check = (condition, message) => { if (!condition) errors.push(message) }
const idPattern = /^[A-Za-z0-9_-]+$/
const ids = (items) => new Set(items.map((item) => item.id))
const knowledgeIds = ids(knowledge)
const conceptIds = ids(concepts)
const tagIds = ids(tags)
const symbols = new Set(['lotus', 'bamboo', 'water', 'narrative'])
const properties = new Set([
  'geometry.cx', 'geometry.cy', 'geometry.r',
  'transform.x', 'transform.y', 'transform.rotation', 'transform.scaleX', 'transform.scaleY',
  'style.fill', 'style.stroke', 'style.strokeWidth', 'style.opacity',
])

check(concepts.length === 8, `expected 8 concepts, got ${concepts.length}`)
check(tags.length === 9, `expected 9 tags, got ${tags.length}`)
check(knowledge.length === 12, `expected 12 knowledge entries, got ${knowledge.length}`)
check(rules.length === 8, `expected 8 rules, got ${rules.length}`)
check(fixtures.length >= 5, `expected at least 5 fixtures, got ${fixtures.length}`)

function checkIds(items, label) {
  const seen = new Set()
  for (const item of items) {
    check(typeof item.id === 'string' && idPattern.test(item.id), `${label}: invalid id ${item.id}`)
    check(!seen.has(item.id), `${label}: duplicate id ${item.id}`)
    seen.add(item.id)
  }
}

checkIds(concepts, 'concepts')
checkIds(tags, 'tags')
checkIds(knowledge, 'knowledge')
checkIds(rules, 'rules')

function checkTree(items, label) {
  const itemIds = ids(items)
  for (const item of items) {
    if (item.parentId !== null) {
      check(itemIds.has(item.parentId), `${label} ${item.id}: missing parent ${item.parentId}`)
    }
    const seen = new Set()
    let current = item
    let depth = 0
    while (current?.parentId) {
      check(!seen.has(current.id), `${label} ${item.id}: parent cycle`)
      seen.add(current.id)
      depth += 1
      current = items.find((candidate) => candidate.id === current.parentId)
    }
    check(depth < 10, `${label} ${item.id}: suspicious parent depth`)
  }
}
checkTree(concepts, 'concept')
checkTree(tags, 'tag')

for (const tag of tags) {
  for (const conflict of tag.conflictsWith ?? []) {
    check(tagIds.has(conflict), `tag ${tag.id}: missing conflict ${conflict}`)
    const other = tags.find((item) => item.id === conflict)
    check(other?.conflictsWith?.includes(tag.id), `tag ${tag.id}: conflict ${conflict} is not symmetric`)
  }
}

for (const item of knowledge) {
  check(['person', 'story', 'concept', 'symbol'].includes(item.type), `knowledge ${item.id}: invalid type`)
  check(item.status === 'active' || item.status === 'inactive', `knowledge ${item.id}: invalid status`)
  check(item.name.length >= 1 && item.name.length <= 80, `knowledge ${item.id}: invalid name length`)
  check(item.summary.length <= 500, `knowledge ${item.id}: summary too long`)
  check(item.content.length <= 20000, `knowledge ${item.id}: content too long`)
  check((item.aliases ?? []).length <= 20, `knowledge ${item.id}: too many aliases`)
  for (const conceptId of item.conceptIds ?? []) check(conceptIds.has(conceptId), `knowledge ${item.id}: missing concept ${conceptId}`)
  for (const tagId of item.tagIds ?? []) check(tagIds.has(tagId), `knowledge ${item.id}: missing tag ${tagId}`)
  for (const relation of item.relations ?? []) {
    check(['embodies', 'associated_with', 'symbolizes', 'derived_from'].includes(relation.predicate), `knowledge ${item.id}: invalid predicate`)
    check(knowledgeIds.has(relation.targetEntityId), `knowledge ${item.id}: missing relation target ${relation.targetEntityId}`)
  }
  for (const source of item.sourceReferences ?? []) {
    check(typeof source.title === 'string' && source.title.length >= 1 && source.title.length <= 200, `knowledge ${item.id}: invalid source title`)
    check(source.url === null || /^https?:\/\//.test(source.url), `knowledge ${item.id}: invalid source url`)
    check(typeof source.note === 'string' && source.note.length <= 500, `knowledge ${item.id}: invalid source note`)
  }
}

function validateParameters(parameters, label) {
  check(Array.isArray(parameters), `${label}: parameterSchema must be an array`)
  check(parameters.length <= 16, `${label}: too many parameters`)
  const names = new Set()
  for (const [index, parameter] of parameters.entries()) {
    const prefix = `${label} parameter #${index + 1}`
    check(/^[a-zA-Z][a-zA-Z0-9_]{0,39}$/.test(parameter.name ?? ''), `${prefix}: invalid name`)
    check(!names.has(parameter.name), `${prefix}: duplicate name ${parameter.name}`)
    names.add(parameter.name)
    check(typeof parameter.label === 'string' && parameter.label.length >= 1 && parameter.label.length <= 80, `${prefix}: invalid label`)
    check(typeof parameter.description === 'string' && parameter.description.length <= 300, `${prefix}: invalid description`)
    check(['number', 'integer', 'enum', 'boolean', 'color'].includes(parameter.type), `${prefix}: invalid type`)
    if (parameter.type === 'number' || parameter.type === 'integer') {
      for (const key of ['default', 'min', 'max', 'step']) check(typeof parameter[key] === 'number' && Number.isFinite(parameter[key]), `${prefix}: ${key} is not a number`)
      check(parameter.min <= parameter.max, `${prefix}: min > max`)
      check(parameter.default >= parameter.min && parameter.default <= parameter.max, `${prefix}: default outside range`)
      check(parameter.step > 0, `${prefix}: step <= 0`)
      if (parameter.type === 'integer') {
        for (const key of ['default', 'min', 'max', 'step']) check(Number.isInteger(parameter[key]), `${prefix}: ${key} is not integer`)
      }
    }
    if (parameter.type === 'enum') {
      check(Array.isArray(parameter.options) && parameter.options.length >= 1 && parameter.options.length <= 20, `${prefix}: invalid options`)
      check(parameter.options.includes(parameter.default), `${prefix}: default not in options`)
    }
    if (parameter.type === 'boolean') check(typeof parameter.default === 'boolean', `${prefix}: default not boolean`)
    if (parameter.type === 'color') check(/^#[0-9A-Fa-f]{6}$/.test(parameter.default ?? ''), `${prefix}: invalid color`)
  }
  return names
}

function validateIR(ir, label) {
  check(ir && typeof ir === 'object', `${label}: IR must be an object`)
  check(ir.schemaVersion === '1.0', `${label}: invalid schemaVersion`)
  check(ir.canvas?.width === 800 && ir.canvas?.height === 600, `${label}: canvas must be 800x600`)
  check(Number.isInteger(ir.viewport?.x) && Number.isInteger(ir.viewport?.y), `${label}: viewport x/y must be integers`)
  check(ir.viewport?.width >= 1 && ir.viewport?.width <= 800, `${label}: viewport width out of range`)
  check(ir.viewport?.height >= 1 && ir.viewport?.height <= 600, `${label}: viewport height out of range`)
  check(ir.durationMs === 0 || (ir.durationMs >= 1000 && ir.durationMs <= 5000), `${label}: invalid duration`)
  check(Array.isArray(ir.nodes) && ir.nodes.length <= 200, `${label}: nodes must be an array <= 200`)
  const nodeIds = new Set()
  const nodeById = new Map()
  let vertices = 0
  for (const node of ir.nodes ?? []) {
    check(idPattern.test(node.id ?? ''), `${label}: invalid node id`)
    check(!nodeIds.has(node.id), `${label}: duplicate node id ${node.id}`)
    nodeIds.add(node.id)
    nodeById.set(node.id, node)
    check(['group', 'circle', 'polygon', 'polyline'].includes(node.type), `${label}: invalid node type ${node.type}`)
    if (node.parentId !== null) check(nodeById.has(node.parentId), `${label}: node ${node.id} references a later/missing parent ${node.parentId}`)
    if (node.type === 'circle') {
      check(typeof node.geometry?.r === 'number' && node.geometry.r > 0 && node.geometry.r <= 2048, `${label}: circle ${node.id} invalid radius`)
    }
    if (node.type === 'polygon' || node.type === 'polyline') {
      check(Array.isArray(node.geometry?.points) && node.geometry.points.length >= 2, `${label}: ${node.id} missing points`)
      vertices += node.geometry?.points?.length ?? 0
    }
    if (node.material) check(node.material.shaderRef === 'flat', `${label}: ${node.id} invalid material`)
  }
  check(vertices <= 2000, `${label}: too many vertices ${vertices}`)
  for (const node of ir.nodes ?? []) {
    let depth = 1
    let current = node
    const seen = new Set()
    while (current?.parentId) {
      check(!seen.has(current.id), `${label}: parent cycle`)
      seen.add(current.id)
      const parent = nodeById.get(current.parentId)
      check(Boolean(parent), `${label}: missing parent ${current.parentId}`)
      check(parent?.type === 'group', `${label}: parent ${current.parentId} is not a group`)
      depth += 1
      current = parent
    }
    check(depth <= 4, `${label}: node ${node.id} depth ${depth} > 4`)
  }
  for (const track of ir.animations ?? []) {
    check(nodeIds.has(track.nodeId), `${label}: animation references missing node ${track.nodeId}`)
    check(['x', 'y', 'rotation', 'opacity'].includes(track.property), `${label}: invalid animation property ${track.property}`)
    check(track.startMs >= 0 && track.endMs > track.startMs && track.endMs <= ir.durationMs, `${label}: invalid animation time range`)
    check(track.easing === 'linear', `${label}: only linear animation is allowed`)
    if (track.property === 'opacity') check(track.from >= 0 && track.to <= 1, `${label}: opacity animation outside 0..1`)
  }
  const seenProperties = new Set()
  for (const track of ir.animations ?? []) {
    const key = `${track.nodeId}:${track.property}`
    check(!seenProperties.has(key), `${label}: duplicate animation track ${key}`)
    seenProperties.add(key)
  }
  for (const entry of ir.narrative ?? []) {
    check(entry.timeMs >= 0 && entry.timeMs <= ir.durationMs, `${label}: narrative time outside duration`)
    for (const entityId of entry.entityIds ?? []) check(knowledgeIds.has(entityId), `${label}: narrative missing entity ${entityId}`)
    check(entry.caption.length <= 200, `${label}: narrative caption too long`)
  }
}

function validateTemplate(path, data, label) {
  const template = data.template
  check(template?.id && idPattern.test(template.id), `${label}: invalid template id`)
  check(Array.isArray(data.versions), `${label}: versions must be an array`)
  const versionIds = ids(data.versions ?? [])
  check(versionIds.has(template.publishedVersionId), `${label}: publishedVersionId missing`)
  const published = data.versions?.find((version) => version.id === template.publishedVersionId)
  check(published?.status === 'published', `${label}: published version is not published`)
  check(versionIds.has(template.latestVersionId), `${label}: latestVersionId missing`)
  for (const version of data.versions ?? []) {
    const versionLabel = `${label} ${version.id}`
    check(version.id && idPattern.test(version.id), `${versionLabel}: invalid id`)
    check(version.versionNumber >= 1, `${versionLabel}: invalid version number`)
    check(['draft', 'submitted', 'published', 'rejected', 'archived'].includes(version.status), `${versionLabel}: invalid status`)
    check(version.generatorRef === 'ir', `${versionLabel}: generatorRef must be ir`)
    check(version.fragmentSource === null, `${versionLabel}: fragmentSource must be null`)
    check(Array.isArray(version.supportedBackends) && version.supportedBackends.length === 1 && version.supportedBackends[0] === 'canvas2d', `${versionLabel}: invalid backends`)
    check(Array.isArray(version.constraints), `${versionLabel}: constraints must be an array`)
    for (const symbol of version.metadata?.symbols ?? []) check(symbols.has(symbol), `${versionLabel}: invalid symbol ${symbol}`)
    for (const entityId of version.metadata?.entityIds ?? []) check(knowledgeIds.has(entityId), `${versionLabel}: missing entity ${entityId}`)
    for (const tagId of version.metadata?.tagIds ?? []) check(tagIds.has(tagId), `${versionLabel}: missing tag ${tagId}`)
    const parameterNames = validateParameters(version.parameterSchema, versionLabel)
    validateIR(version.irTemplate, versionLabel)
    const nodeIds = new Set((version.irTemplate?.nodes ?? []).map((node) => node.id))
    const bindingKeys = new Set()
    for (const binding of version.bindings ?? []) {
      const key = `${binding.parameter}:${binding.nodeId}:${binding.property}`
      check(!bindingKeys.has(key), `${versionLabel}: duplicate binding ${key}`)
      bindingKeys.add(key)
      check(parameterNames.has(binding.parameter), `${versionLabel}: binding missing parameter ${binding.parameter}`)
      check(nodeIds.has(binding.nodeId), `${versionLabel}: binding missing node ${binding.nodeId}`)
      check(properties.has(binding.property), `${versionLabel}: binding invalid property ${binding.property}`)
    }
  }
}

validateTemplate('shared/demo/templates/lotus.json', lotus, 'lotus')
validateTemplate('shared/demo/templates/bamboo.json', bamboo, 'bamboo')
validateTemplate('shared/demo/templates/narrative.json', narrative, 'narrative')

const statuses = [...lotus.versions, ...bamboo.versions, ...narrative.versions].map((version) => version.status)
check(statuses.includes('rejected'), 'demo templates need a rejected version')
check(statuses.includes('archived'), 'demo templates need an archived version')

for (const rule of rules) {
  const label = `rule ${rule.id}`
  check(rule.name.length >= 1 && rule.name.length <= 80, `${label}: invalid name`)
  check(typeof rule.enabled === 'boolean', `${label}: enabled must be boolean`)
  check(rule.priority >= 0 && rule.priority <= 1000, `${label}: priority out of range`)
  check(['any', 'all'].includes(rule.match?.operator), `${label}: invalid operator`)
  check(Array.isArray(rule.match?.keywords) && Array.isArray(rule.match?.entityIds)
    && Array.isArray(rule.match?.conceptIds) && Array.isArray(rule.match?.tagIds), `${label}: invalid match arrays`)
  check([rule.match.keywords, rule.match.entityIds, rule.match.conceptIds, rule.match.tagIds].some((array) => array.length > 0), `${label}: empty match`)
  for (const entityId of rule.match.entityIds) check(knowledgeIds.has(entityId), `${label}: missing entity ${entityId}`)
  for (const conceptId of rule.match.conceptIds) check(conceptIds.has(conceptId), `${label}: missing concept ${conceptId}`)
  for (const tagId of rule.match.tagIds) check(tagIds.has(tagId), `${label}: missing tag ${tagId}`)
  check(Array.isArray(rule.effects?.symbolIds) && rule.effects.symbolIds.every((symbol) => symbols.has(symbol)), `${label}: invalid effects symbols`)
  check(Array.isArray(rule.effects?.palette) && rule.effects.palette.length >= 1 && rule.effects.palette.length <= 8, `${label}: invalid palette size`)
  for (const color of rule.effects?.palette ?? []) check(/^#[0-9A-Fa-f]{6}$/.test(color), `${label}: invalid palette color ${color}`)
  check(['centered', 'vertical', 'horizontal', 'radial'].includes(rule.effects?.composition), `${label}: invalid composition`)
  check(['none', 'sway', 'reveal'].includes(rule.effects?.motion), `${label}: invalid motion`)
  check(rule.effects?.caption?.length <= 200, `${label}: caption too long`)
  check(rule.explanation?.length <= 500, `${label}: explanation too long`)
}

for (const [index, ir] of fixtures.entries()) validateIR(ir, `fixture #${index + 1}`)
check(fixtures.some((ir) => ir.durationMs === 4000), 'expected a 4000ms narrative fixture')
check(fixtures.some((ir) => ir.viewport.width < ir.canvas.width || ir.viewport.height < ir.canvas.height), 'expected a cropped viewport fixture')

if (errors.length > 0) {
  console.error(`demo-data-check: ${errors.length} error(s)`)
  for (const error of errors) console.error(`- ${error}`)
  process.exit(1)
}

console.log('demo-data-check: OK')
console.log(`concepts=${concepts.length} tags=${tags.length} knowledge=${knowledge.length} templates=3 rules=${rules.length} fixtures=${fixtures.length}`)
console.log(`knowledge relation targets=${new Set(knowledge.flatMap((item) => item.relations.map((relation) => relation.targetEntityId))).size}`)
