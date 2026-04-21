# OCR And Screenshot

- 默认遵守 screenshot-first：只要命令返回了截图，就先读截图，再消费压缩后的文本描述。
- 当结果里出现 `shot path=...` 时，说明本轮已经生成了可直接查看的原始截图。
- 如果命令返回 `shot path=...` 且紧随 `info read_image_first=1`，必须先读取这张原始截图，再参考后续 OCR、detect、status 或其他压缩文本。
- OCR 的角色是补充可检索文本、box 与结构化线索，不是替代原始画面。
- 总入口或 scene entry 在继续推进前，应把截图和 OCR 结合起来判断当前局面，而不是只看 OCR 文本就直接行动。
