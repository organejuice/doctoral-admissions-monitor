# 全国博士招生监控：RSSHub与原文采集试点

以固定高校/研究所目录为输入，建立机构到官方来源的映射，通过真实RSSHub输出订阅，再采集正文与附件并保留版本。当前为26源试点，不代表全国已上线或当前申请周期已补采完整。

## 数据和边界

- 本地身份基线为 `doctoral-catalogues-20261110-v1`，现行7文件只读，采集程序启动时检查哈希。985/211/双一流保持独立重叠，研究所代码和前导零保持字符串。
- `config/source_registry.json` 保留全部3547条分层身份、926个已登记官方/发现来源和1163条绑定；缺口仍保留。来源数不等于已通过采集的数量。
- `config/pilot_sources.json` 选择26个来源，覆盖高校、多体系研究所、HTML/CMS、公开接口、PDF/Word/图片、院系与共享列表，并包含两个已知失败源。
- RSSHub固定官方包/提交，版本及AGPL许可证在 `integrations/rsshub/README.md` 和 `LICENSE.RSSHub`。上游核心未修改；定制白名单路由向回环来源桥取结构化数据，再由真实RSSHub渲染RSS。公开官网发现、分页和原始响应归档由Python适配层负责，不把此模式冒称26个上游现成路由。
- 另实际比较了官方原生地科院路由与公开API首屏10条。其feed摘要不替代正文，也不证明历史全部覆盖。

每源本次最多2页、2篇公告、每篇8个附件，单响应16MiB；这些是可审查的试点预算。未读页、未下载附件和失败均保留。扫描图、混合PDF的空文本页、旧Word和压缩包原件与文本提取状态分开。机器日期、批次及对象判断仍需核对原文，不据候选日期直接发截止提醒。

## 本地运行

Python 3.11，依赖版本见 `requirements.txt`；本机完整环境锁见 `deploy/conda-win-64.explicit.txt` 与 `deploy/pip-freeze-win.txt`。RSSHub使用Node24.22.1和固定npm锁。现有环境复用或离线包可优先使用，独立开发环境不要修改其他项目。

```sh
python -m pip install -r requirements.txt
npm --prefix integrations/rsshub ci --ignore-scripts --omit=dev --no-audit --no-fund
python -m unittest discover -s tests -v
```

分别启动来源桥、RSSHub和一次采集；三者使用相同数据目录，每轮桥与采集使用同一个新的run-id。桥只在单次轮询内缓存，下一轮必须重启桥。

```sh
python -m doctoral_monitor bridge --config config/pilot_sources.json --data-dir data --run-id pilot-EXAMPLE-01 --port 8765
node integrations/rsshub/server.mjs
python -m doctoral_monitor run --config config/pilot_sources.json --data-dir data --run-id pilot-EXAMPLE-01 --rsshub http://127.0.0.1:1200
```

RSSHub在 `http://127.0.0.1:1200/doctoral/{source_id}`，来源桥仅监听127.0.0.1:8765且拒绝未知ID。不向公网暴露任意URL转换接口。失败来源返回错误，不用空RSS冒充成功。

本地原始HTTP响应按内容SHA256保存在 `data/raw/http`，逐次传输元数据在 `data/raw/runs`；RSS也保存原字节。公告版本、逐次观察、SQLite检查点、双通道outbox和报告在data目录。全文/附件不齐不会覆盖旧版本。HTML语义无变化时仍保存原始HTTP观察；主PDF/Word/图片与附件的原件变更使用字节哈希判版本。

## 通知

默认不发送。个人收件人、SMTP凭据位置和ntfy话题在本地private配置或环境/受控Secrets中，禁止放入公开代码和数据包。队列按事件和通道独立重试；SMTP/API接受不代表手机已收到或用户已读。

```sh
python -m doctoral_monitor.notifications --data-dir data --test-only
python -m doctoral_monitor.notifications --data-dir data --private-config private/notifications.json --test-only --send
```

第二条仅在本人收件地址及手机订阅已确认、私有配置启用且凭据已安全设置后使用。测试事件与招生公告队列隔离。ntfy手机安装/订阅与HTTP发布遵循[官方手机说明](https://docs.ntfy.sh/subscribe/phone/)和[官方发布接口](https://docs.ntfy.sh/publish/)。锁屏实际收到需要用户实机验收。

## 免费云端试点

部署入口及发布白名单在deploy。当前workflow仅手动触发，标准公共Linux runner、固定超时和容量闸门，不启用付费接口或自动收费回退。公开软件包与私有原文/状态隔离；私有仓库读写使用只授权该仓库的独立凭据，不复制个人CLI广域token。

公开发布副本仅包含白名单软件、测试、公开来源配置和脱敏的基线契约；移除本机绝对路径，保留原冻结manifest哈希。云端以导出来源登记表与脱敏manifest哈希核对基线，不宣称重新读取了本地7张原表。

私有Release归档采用受限分片并上传后真实下载恢复校验。首次启动必须显式bootstrap；断网、权限失败和缺检查点不能冒作空状态。Artifacts/cache不作为唯一永久原文库。仓库名称、公开内容和受限凭据配置确定前不发布私人资料；实际云端出口、恢复、零费用和手机送达另行验收，不以本地成功代替。

## 复现与审计

`params.yaml`记录预算，Git记录软件，固定基线/来源配置/运行代码SHA保存在运行证据和audit。原页、时间、实际状态、失败和解析结果均可追溯。测试覆盖访问挑战、HTTP200占位、API导航壳、嵌入CDATA、分页旧日期插入、正文/懒加载图片、同URL正文/附件修订、共享绑定、双通道独立失败与重复发送。

本次为应用开发的探索性验证，不作正式科研严格复现或全国零漏报声明。WikiSkill仅保存项目本地执行轨迹；未使用付费模型API，未演化或跨项目晋级技能。网络仅用于公开来源及开源依赖，数据本地处理；连续7天运行观察不包含在一次性手动试点的完成声明中。
