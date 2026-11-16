# Q1 标注与真实 PNG 校准集

这是 **12 个手工预期标签（6 正常、6 坏例）**、真实 Chrome Canvas PNG 和 6 个坏例的改进后 IR/PNG，不是 API 返回 fixture，也不是审美或赛题评分基准。`cases.json` 保留标签理由、PNG变体、原IR、修正IR与预期状态；`png/manifest.json` 给出版本、时刻和SHA256。两组动画各含0/2000/4000ms，共24张必要PNG。

阈值在执行标注校准前固定为：交叠0.12、归一化RGB色板距离0.12、杂散像素比例0.02；200×150几何mask、一格膨胀、前景阈值8。算法初版不返回校准分数。源标签独立于算法实现，误报/漏报记录在本轮共享交接，不为追求全过修改标签；12样本结果不代表泛化表现。

覆盖：分离主体、同组设计交叠、TRS/裁剪、细描边、透明填充、正常动画、独立主体交叠、色板外蓝色、几何外杂点、空白PNG、出界图元、中间时刻碰撞。噪声/空白通过**真实 PNG 的像素修改**产生，IR保持合法。颜色依据使用固定的来源色板（与演示莲规则相同）；真实服务测试还须从实际语义生成来源读取色板，不能由客户端提交自评分或伪造provenance。

生成与验证（输出必须显式指向本地日志）：

```text
node frontend/tests/make-quality-samples.mjs
node frontend/tests/quality-samples-render.mjs <local-output>
python -m unittest test_assessment
```

首个命令只重建声明式IR/标签文件；第二个命令调用真实适配器并输出PNG/manifest到本地，核验后才挑选必要PNG更新本目录。后端测试校验内容hash、原/改进后标签、score=null、证据节点/时刻与容量边界。不得把本目录的期望标签注入生产质量报告，生产报告由服务端读取存储的IR、来源和上传PNG独立计算。
