# 公开程序图片及来源

[返回 README](../../README.md)。这些图片于 2026-11-09 从已完成流水线证据中选择，逐张检查后原字节复制，没有裁剪、遮挡、重绘或重新编码。机器可读来源与 SHA-256 见 [provenance.json](provenance.json)。`source` 是本地证据的仓库相对标识，不是公开下载链接；原始目录受 Git 忽略保护。

## 图片分类

| 公开图片 | 真实来源与工况 | 可以说明的内容 |
| --- | --- | --- |
| [analysis-mainwindow.jpg](analysis-mainwindow.jpg) | 后处理流水线的 `testing-r3-parent-native-source/actual-source-mainwindow-strain.jpg`；官方源码入口的原生整窗 | SHORT QA 第3接受帧、288混凝土样本，真实应变、方向轴、科学刻度及曲线联动；不是M50，早于图标接入 |
| [desktop-model-preview.jpg](desktop-model-preview.jpg) | 图标流水线的 `testing-parent-native/actual-exe-mainwindow-final.jpg`；实际EXE原生整窗 | 已检查默认模型的输入预览及标题栏图标；没有分析结果 |
| [m50-step269-concrete-stress.png](m50-step269-concrete-stress.png) | 后处理R3的 `testing-r3-m50-bundled/M50-step-0269-concrete_stress-true-vtk.png` | 原完整M50保存帧269的真实BeamViewport组件导出，混凝土轴向应力/MPa |
| [m50-step269-concrete-strain.png](m50-step269-concrete-strain.png) | 同目录 `M50-step-0269-concrete_strain-true-vtk.png` | 同帧组件导出，混凝土轴向总机械应变/1，含自重 |
| [short-mainwindow-viewport-strain.png](short-mainwindow-viewport-strain.png) | 后处理R3的 `testing-r3-mainwindow-export/short-mainwindow-strain-true-vtk.png` | 正常MainWindow控制器打开完整SHORT工程后，所属视口导出；不是整窗或M50 |

后处理流水线为 `20261109-072002-m9zy`，图标流水线为 `20261109-125223-priw`，均正式 COMPLETE。筛选依据是各自 `testing-report.md`、`test-manifest.json`、`verification-report.md`，后处理 `program-image-provenance.json`、`testing-r3-final-sealed/manifest.json`，以及图标 `testing-final-sealed/manifest.json` 与实际原生观察记录。具体封存目录以本地清单为准；公开副本身份由本目录 JSON 精确列明。

## 工况与显示含义

SHORT 是专用完整原生 QA 工况：M100、4层纤维、不计自重及诊断阈值；第3接受帧应变范围 `-7.5320079860443955E-06 … 7.298867158029096E-06`，变形倍率1。源码原生整窗与R3包内主窗口视口导出来源不同，不能互换标签；这里没有选用另一张R3包运行时整窗照片，也没有将验证辅助程序的进程冒充CMD入口进程。

M50 图片读取既有完整工程的第269接受帧，5280个真实保存样本/场（实际mapper为95040个double单元标量），变形倍率1。此帧在记录路径上到达物理标距判据终止，合计荷载约 `3.668E+04 N`。原始范围为：

| 场 | 最小值 | 最大值 | 单位 |
| --- | --- | --- | --- |
| 混凝土纤维轴向应力 | -28.895513955741258 | 2.49983101877594 | MPa |
| 混凝土纤维轴向总机械应变 | -0.0033306889489242673 | 0.009886478618353785 | 1 |

拉正压负；纵向按最近实际高斯截面、高度按纤维层代表分块。宽度方向仅为外观，非三维实体场。总机械应变含自重，不等于物理边缘标距平均应变。每帧色标按实际范围，白色不保证零。纵筋/箍筋不属于混凝土色标。图片来自真实程序对保存状态的显示，没有新增求解或离线绘图；组件图附带Unicode来源说明，不能冒充完整主窗口。

## 公开范围检查

选中图片仅含默认/QA模型显示、程序控制、必要结果数值和稳定身份哈希，未见个人账户、绝对本地路径、原始试验表或私有文档。公开这些必要结果示例不等于公开输入、完整状态、CSV或试验资料；默认假设与合成数据不冒充实测。

已查看但未复制资源管理器大图标及文件/任务栏整桌面图：它们包含个人账户、目录导航和其他应用内容。未通过裁剪或涂改将其重新包装为原始证据。图标已用于EXE/Qt/任务栏/Explorer的结论来自已完成测试与独立核验，公开图仅展示其中主窗口；品牌图标不是计算云图。

本地两份正式14/13页PDF及663项归档保持封存，不在此目录。STRICT失败、真实试验C未验证、其他历史D缺口及限定研究资格范围保持，详见 [README](../../README.md#科学验证限制)。
