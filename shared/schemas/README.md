# 共享数据契约（开发者 A）

本目录用于后续 IR、参数、导入包等跨端数据约束，当前尚无独立 schema 实现。

当前 HTTP 字段与模型的唯一依据是 [核心 OpenAPI](../../docs/openapi-core.json)，业务条件见 [接口文档](../../docs/接口文档.md)。不要手工复制整份 OpenAPI 到此目录，避免维护两套契约。

后续新增共享 schema 时注明版本和与 OpenAPI 的关系，由 A 维护，B 通过真实请求响应和 fixture 反馈。完整 OpenAPI 中的选做模型不代表已实现能力。
