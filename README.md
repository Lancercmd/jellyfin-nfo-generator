需搭配 [kookxiang/jellyfin-plugin-bangumi](https://github.com/kookxiang/jellyfin-plugin-bangumi) 使用。

`Python 3.10` `requests`

为了便于判断番剧的年份和季度，我为番剧文件夹命名的方式是 `番剧名 (YYYYQX)`，所以通常情况下插件无法正确获取到番剧名。并且我也不太能接受为了让 Jellyfin 能够自动刮削去重命名视频文件。

因为不同压制组或字幕组的命名规则也有所不同，`jellyfin-plugin-bangumi` 插件即使启用 `使用 AnitomySharp 猜测集数` 也有难以做到精确的时候。

使用这个脚本，就可以在媒体库扫描前通过 nfo 文件预定义 `<bangumiid>`，再配合 `jellyfin-plugin-bangumi` 插件的 `始终根据配置的 Bangumi ID 获取元数据` 就能总是根据已绑定的 `subject_id` 或 `episode_id` 获取到对应番剧或剧集的元数据。

以下为假设和我一样是**每一个季度都算做一个番剧**的话，因为 Bangumi 就是这样的。

脚本依赖 `Python 3.10` 和 `requests`，更早版本的未测试，以实际运行情况为准。

脚本启动时会先通过 Bangumi 进行用户验证，故启动前需要先在脚本中填写自己的 `APP_ID` 和 `APP_SECRET`，在 [Bangumi 开发者平台](https://bgm.tv/dev/app) 新建一个应用并获取。

验证完成后，向命令行界面输入**于根含有番剧视频文件的**目录绝对路径，便会
1. 尝试从文件夹名匹配番剧名。
1. 通过调用 Bangumi API 匹配番剧和话数，有多个搜索结果时交互地进行选取。
1. 保存到对应 nfo 文件（番剧信息 `tvshow.nfo`；剧集信息 `剧集文件名.nfo`）。

对于季度第一话不为 `ep.01` 的情况，会使用自动偏置及交互地修正的方式进行处理。

对于生成 nfo 文件时提示权限不足的情况，可能是于容器运行的 Jellyfin 使用更高权限更早生成了 nfo 文件，将会跳过生成。

用户验证信息会以 `bangumi.json` 保存到脚本的运行路径，以在下一次运行时判断是否需要从头进行用户验证，或者仅自动刷新续期授权。

对于此处未能解释详尽的地方，请以源代码为准。对于 Bangumi API 的信息，请以 [bangumi/api](https://github.com/bangumi/api) 为准。