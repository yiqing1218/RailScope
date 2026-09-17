# RailScope Git workflow

在 `D:\RailScope` 完成任何代码、桌面界面、测试或文档修改后，必须在同一任务内：

1. 运行与修改范围相符的验证；
2. 检查 `git diff`，确认不包含 `.env`、原始 OSM 数据或其他大型生成数据；
3. 将本次修改作为一个独立的本地 Git 提交。

提交信息使用简短中文说明。不要提交 `data/raw/` 的下载数据和 `data/processed/` 的可再生 GIS 输出；这些文件通过导入脚本生成。
