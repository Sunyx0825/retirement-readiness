# 运行与接口

## 输入

输入JSON使用`answers`保存问卷回答，可选`followups`与`followup_events`保存已确认补充回答和追问状态。三个示例的`meta.synthetic`均为true。公开库没有真实被试数据。

资产、负债和年度收入的数值单位为万元；月度支出单位为元。`inputs.py`同时支持示例中的命名财务记录与问卷的`Qxx__blank_n`字段。两个格式同时提供且冲突时会报错。

问卷定义位于`core/data/questionnaire.json`，包括题目、选项、分支和数值约束。命令默认固定在2026年9月评估，也可用`--as-of YYYY-MM-DD`显式指定月份。

## 真实模型配置

在自己的终端配置兼容Chat Completions且支持JSON输出的模型。以下仅为变量名示例，密钥不应写进源码或提交到仓库。

PowerShell：

```powershell
$env:LLM_API_KEY = '<你的密钥>'
$env:LLM_MODEL = '<模型名称>'
$env:LLM_BASE_URL = 'https://你的服务地址/v1'
python -m retirement_readiness.cli examples/beijing.json --llm
```

macOS/Linux：

```bash
export LLM_API_KEY='<你的密钥>'
export LLM_MODEL='<模型名称>'
export LLM_BASE_URL='https://你的服务地址/v1'
python -m retirement_readiness.cli examples/beijing.json --llm
```

报告包含`mode`字段：`offline_rules`表示离线规则结果，`live_multi_agent`表示真实模型阶段成功，`offline_fallback`表示AI调用失败后保留的规则结果。最后一种情况使用非零退出码，便于自动化程序发现降级。

## 报告角色和校验

建议选择角色先从规则候选集中挑选建议，必须覆盖已识别问题。之后综合评估、分项说明、评分解读与补充洞察并发运行；分项说明继续细分为情景概览、问题解释和建议解释。每个阶段有独立提示、输出结构及事实引用检查。

结构验证不通过时最多尝试修复一次。引用ID必须来自当前家庭的事实与计算结果；基础金额和评分由Python提供。该机制能约束报告结构和引用范围，不能自动证明每一句生成文字的语义正确。

## 测试

离线测试不调用付费模型。它们覆盖输入金额保真、互斥选项、账户耗尽、情景比例、报告与计算一致性、模型失败处理和调用上下文隔离。网络接口与报告阶段可通过测试替身验证契约；实际服务的语言质量取决于配置的模型。

公开版本提供研究核心与命令行演示，不依赖原业务平台的用户系统、数据库、任务队列或部署配置。
