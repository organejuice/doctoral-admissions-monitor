# 免费云端试点交接

当前产物是可审查的软件发布工具和手动试点工作流。没有创建或发布仓库，没有运行云端任务，没有上传原文，也没有发送通知。真实云端可达性、私有Release恢复、邮件和手机锁屏接收仍须实测。

## 软件与私有数据

只把 `prepare_publication.py` 生成的白名单目录用作公共软件仓库输入，禁止从申博工作区根目录直接推送。允许源代码、合成测试、公共来源配置、RSSHub集成及锁文件、本文档和部署工具；排除个人配置、`.env`、通知收件人/推送topic、raw/state/archives/data、审计、已有Git历史、Node依赖和Conda环境。发布副本会移除来源探针的Cookie/Set-Cookie及Authorization响应头键，原始本地配置和证据保留。原始招生材料及状态仅进入单独的私有数据仓库。

发布前运行（以监控项目为当前目录，目标须是尚不存在的新目录）：

```powershell
python deploy/prepare_publication.py --root . --destination <独立可审查目录> --baseline-manifest ../catalogue_baseline/manifest.json
```

导出只改发布目录内的配置，新增 `baseline/manifest.json`：移除本机绝对路径字段 `delivery_root`，另存 `original_manifest_sha256` 保留原冻结清单指纹。把 `config/pilot_sources.json` 的 `baseline.validation_mode` 设为 `exported_registry`，锚定脱敏清单实际SHA256、原清单SHA256和来源registry的SHA256。云端只验证这两个导出文件及基线ID，没有重新读取本机7份大表；本地仍按完整底表验证。源配置和冻结底表不会被改写。

审查 `.publication-manifest.json` 的逐文件路径、大小、SHA256后，再选择公共软件仓库及私有数据仓库。工具不改变任何既有仓库的可见性。已有仓库若角色不匹配会失败。

```text
python deploy/cloud_control.py create-repositories --code-repo OWNER/CODE --data-repo OWNER/DATA
python deploy/cloud_control.py publish --repo OWNER/CODE --staging REVIEWED_DIRECTORY
```

这些命令会真实创建和发布，只在具体仓库及发布清单已经确定后执行。工具使用官方 `gh` CLI的受控凭据或 `GH_TOKEN`，不在命令行、源码或日志中放密钥。

## 首次手动云端运行

发布仓库需设置变量 `DATA_REPOSITORY=OWNER/DATA`，以及仅有目标私有数据仓库Contents读写权限的受控凭据 `DATA_REPOSITORY_TOKEN`。公共软件仓库自己的只读 `GITHUB_TOKEN` 不能自动访问另一个私有仓库。不要把个人邮箱、密码或推送topic填入公共来源配置；当前工作流只采集和保存，不发送消息。

```text
python deploy/cloud_control.py dispatch --repo OWNER/CODE --workflow pilot.yml --ref main --bootstrap
python deploy/cloud_control.py run-status --repo OWNER/CODE --run-id RUN_ID
```

`--bootstrap`仅用于私有数据仓库确实没有既存快照的首次运行；以后省略。即使带此参数，只要有既存快照仍会优先恢复。读取失败或权限不足不会被当成“没有状态”。工作流仅手动触发，在公共仓库标准 `ubuntu-24.04` runner运行，串行、25分钟超时，没有启用付费runner、Actions artifact或cache。

工作流安装固定Python依赖和RSSHub依赖，执行确定性测试，恢复原始状态，临时启动本地bridge与实际RSSHub，运行真实官网采集，停止服务后把状态和原文归档到私有Release。采集失败时仍尝试保留已有状态和失败报告。Release归档失败会使运行失败，不写成成功持久化。

## 原文包恢复

```text
python deploy/cloud_control.py release-put --repo OWNER/DATA --directory DATA_DIRECTORY --tag snapshot-UNIQUE_RUN_ID
python deploy/cloud_control.py release-get --repo OWNER/DATA --destination NEW_RESTORE_DIRECTORY
```

打包保留原始字节，单文件/分片未压缩预算50MiB，单次数据512MiB，私有仓库全部Release资产自设预算2GiB。超过预算即停止新上传，不自动删除旧原件，不自动收费扩容。这些是试点的工程限制，不是GitHub承诺的免费存储额度。当前实现为完整检查点；全国持续运行前应改为按内容哈希增量包并验证保留策略，避免重复全量归档。

上传先建draft Release，上传SHA256清单和分片，再真实下载、按文件恢复并验证全部哈希，通过后才发布。不会覆写已有tag。恢复目录必须不存在，拒绝路径穿越、符号链接、清单外文件、哈希错误或超额展开；校验通过后才把临时目录提交为目标。`DOCTORAL_CLOUD_TMPDIR`可指定可写且有足够空间的临时目录。

## 本地环境与检查

独立Conda环境为 `D:/conda/envs/doctoral-monitor-pilot`，Python3.11.16。44个Conda包按既有缓存精确URL与MD5离线建立，仅额外下载349KB的 `pypdf==6.14.2`。Windows锁见 `conda-win-64.explicit.txt` 和 `pip-freeze-win.txt`；前者重建Conda主体，再补固定pypdf。云端Linux使用根目录锁定的 `requirements.txt`，不拿Windows包URL在Linux安装。

```text
python -m unittest discover -s deploy -p test_cloud_tools.py -v
```

18项本地测试覆盖原文字节/前导零恢复、深层SHA目录的Windows长路径恢复、损坏分片拒绝、路径穿越拒绝、清单外文件拒绝、个人配置及私钥块排除、已有目录保护、发布白名单与上游许可证保留、导出基线及错误不回显私密内容；模拟GitHub还覆盖上传后下载校验再发布、下载损坏保持draft、连接失败不能当空状态、禁止向公共仓库上传私有包和探针dispatch不携带归档初始化参数。通过不代表真实云端私有Release或手机已经验收。

每次手动云端任务都新启bridge与RSSHub，并给bridge和采集器相同的运行ID。同一个bridge进程只按来源刷新一次，若在本地同进程反复跑采集，应重启bridge或显式刷新其来源缓存，不能把重复读取缓存当新一次在线检查。

## 无私有凭据时的独立出口探针

`public-network-probe.workflow.yml`单独发布为 `.github/workflows/network-probe.yml`，仅手动触发，不使用私有仓库或secret。运行 `python deploy/cloud_control.py dispatch --repo OWNER/CODE --workflow network-probe.yml` 即可在标准公共runner检查当前26源的首屏和每源最多1篇原文。安全精简的逐源HTTP结果、失败原因和计数以 `CLOUD_PROBE_DETAIL=` 单行JSON保存在实际job日志，不输出正文、响应头或个人配置。所有原件只在临时runner存在，未上传Release/artifact/cache，未发通知；这不算持久归档验收。正常归档工作流仍在受限data凭据缺失时失败，不会自动降级成此探针。

## 已核实的平台条件

2026-11-10核对：公共仓库的标准托管runner计算免费；私有仓库有额度与超额计费，故不借用当前私人科研仓库。[Actions计费](https://docs.github.com/en/actions/concepts/billing-and-usage)、[标准runner](https://docs.github.com/en/actions/reference/runners/github-hosted-runners)

定时任务可能延迟或丢弃，默认分支运行，公共仓库60天无活动可能自动停用；本工作流尚未启用定时，后续需外部心跳和连续观察。[调度规则](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)

每个Release最多1000资产、每资产小于2GiB；私有下载需Contents读权限，上传需写权限，下载可能返回200或302，官方gh CLI处理下载。原文不依赖会过期的Actions artifacts作为唯一存储。[Release限制](https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases#storage-and-bandwidth-quotas)、[资产API](https://docs.github.com/en/rest/releases/assets)、[保留期限](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/enabling-features-for-your-repository/managing-github-actions-settings-for-a-repository#configuring-the-retention-period-for-checks-workflow-runs-commit-statuses-artifacts-and-logs-in-your-repository)
