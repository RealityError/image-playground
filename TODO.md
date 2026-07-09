# TODO

## 上游能力吸收

- 收藏夹体系：参考上游多收藏夹交互，但改为服务端持久化，按空间隔离。
- 透明 PNG 后处理：参考上游纯色背景去除思路，优先放在后端处理并保存透明 PNG。
- 流式生成 / 中间图预览：参考上游 partial images 体验，优先由后端接收、落库，前端轮询展示。
- 中等优先级体验优化：导出 loading、复制图片降级、移动端灯箱/长按、Tooltip 溢出、参数显示 overflow、多选交互、纯函数测试补齐。
- 多 provider 第一阶段：已加入管理员可编辑上游与模型配置、API Key 脱敏、普通用户选择模型、参数不支持直接报错、历史记录绑定 provider/model。

## 待讨论

- 多 provider 后续：模型能力 schema 是否需要更细；是否按用户/空间隔离 provider；是否支持非 OpenAI-compatible provider adapter。
