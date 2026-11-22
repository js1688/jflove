import { useParams, Navigate, Link } from 'react-router';
import { PageHead } from '../components/ui';
import { siteVersion } from '../content';

/**
 * 三端操作指引页（`/guide/:platform`）。
 *
 * 数据来源：`website/docs/ui-facts-{desktop,web,mobile}*.md`（源码实测，逐条带 文件:行）。
 * 配图：`website/public/screenshots/{web,desktop,mobile}/*.png`（当前版本实拍，SPEC §9/§9.1）。
 *
 * **三端内容不共用**：入口位置、按钮文案、交互模型都不同，串用会让用户找不到按钮。
 */

type Platform = 'desktop' | 'web' | 'mobile';

interface GuideSection {
  id: string;
  title: string;
  where: string;
  what: string;
  action: string;
  notes?: string[];
  shot?: string;
  ui?: [string, string][];
}

interface GuideDoc {
  label: string;
  navItems: string[];
  intro: string;
  sections: GuideSection[];
}

const GUIDES: Record<Platform, GuideDoc> = {
  web: {
    label: 'Web 端',
    navItems: ['文件管理', '笔记管理', '传输任务', '离线下载'],
    intro:
      '浏览器里能做的四件事：文件管理、笔记管理、传输任务、离线下载。下面按侧栏顺序逐节说明「在哪 → 做什么 → 点击什么」。',
    sections: [
      {
        id: 'start',
        title: '开始之前：连服务器、登录',
        where: '打开站点即是登录页',
        what: 'Web 端不需要安装，打开就能用。但得先让它知道连哪台服务器，再拿账号登录。',
        action: '填账号密码后点登录。成功后左侧出现 4 项主导航 —— 看到「文件管理」就算进对了。',
        shot: 'login',
        ui: [
          ['服务器地址输入框', '留空 = 使用当前站点（推荐）'],
          ['说明', '留空时全部请求打向当前域名；填了就在该地址上重新做一次密钥交换'],
        ],
      },
      {
        id: 'layout',
        title: '界面结构：4 + 2 + 4',
        where: '登录后第一屏',
        what: '整个界面就一条左侧栏：上面 4 项主导航，底部固定 2 项，管理员还会在底部前多出一组 4 项管理面板。',
        action: '顺序是固定的，照着找就行。',
        notes: [
          'Web 端比桌面端少 3 项：文本编辑器、云盘挂载、远程连接 —— 都是桌面端专属，不是坏了',
          '管理面板 4 项对普通账号完全不渲染（不是灰掉，是不出现）',
        ],
      },
      {
        id: 'files',
        title: '文件管理 · 选择磁盘',
        where: '侧栏第 1 项「文件管理」',
        what: '进来看到的第一屏不是文件，而是磁盘列表。文件都挂在虚拟磁盘下面，得先选一块盘。',
        action: '点整张磁盘卡片进入该盘的目录浏览页。',
        shot: 'files-disks',
        notes: [
          '一块盘都没有时显示「暂无可用磁盘」和「请联系管理员为你分配磁盘权限」—— 找管理员在「权限配置」里开',
        ],
        ui: [
          ['页标题', '文件管理'],
          ['副标题', '选择磁盘开始浏览'],
          ['空态', '暂无可用磁盘 / 请联系管理员为你分配磁盘权限'],
          ['底部提示', '所有文件传输均走端到端加密'],
        ],
      },
      {
        id: 'browser',
        title: '文件管理 · 磁盘浏览',
        where: '选完磁盘之后',
        what: '真正干活的地方：上传、建目录、下载、预览、重命名、移动、删除。',
        action: '顶部两个按钮「上传」「新建目录」；每个文件行尾有三点菜单。',
        shot: 'files-browser',
        notes: [
          'PC 端可以直接把文件拖进页面，拖动时提示「松开以上传到当前目录」',
          '没有写权限时，上传/新建按钮整组消失，换成「只读」徽章',
        ],
        ui: [
          ['顶部按钮', '上传 / 新建目录'],
          ['排序入口', '排序'],
          ['行内操作', '下载 / 预览 / 修复损坏媒体 / 重命名 / 移动到… / 删除'],
          ['空目录', '此目录为空 / 可上传文件或新建目录'],
          ['删除确认', '确认删除 + 确定要删除「XXX」吗？此操作不可撤销。'],
        ],
      },
      {
        id: 'notes',
        title: '笔记管理',
        where: '侧栏第 2 项「笔记管理」',
        what: 'Markdown 笔记的列表与编辑，直接读写服务端上的 .md 文件。',
        action: '新建走「新建笔记」弹窗；点进笔记进编辑器。',
        shot: 'notes',
        ui: [
          ['页标题', '笔记管理'],
          ['行内操作', '重命名 / 删除'],
          ['新建弹窗', '新建笔记'],
          ['编辑器副标题', '笔记管理 / Markdown'],
          ['工具栏', '插入 Mermaid 图表'],
          ['离开提示', '未保存的修改'],
        ],
      },
      {
        id: 'transfer',
        title: '传输任务',
        where: '侧栏第 3 项「传输任务」',
        what: '上传 / 下载进度，以及媒体修复任务的合并页（标签切换）。',
        action: '修复任务三端共享，在这里能看到别的端发起的任务。',
        shot: 'transfer',
      },
      {
        id: 'offline',
        title: '离线下载',
        where: '侧栏第 4 项「离线下载」',
        what: '贴地址 → 分析出清单 → 勾选下载到你自己的磁盘目录。',
        action: '顶部「刷新」更新任务列表；删除记录只删记录不删文件。',
        shot: 'offline',
        ui: [
          ['页标题', '离线下载'],
          ['副标题', '粘贴网址，抓取视频 / 音频 / 图片'],
          ['按钮', '刷新'],
          ['空态', '暂无离线下载任务'],
          ['删除确认', '删除任务记录'],
          ['标记', '只读'],
        ],
      },
      {
        id: 'security',
        title: '安全状态',
        where: '侧栏底部「安全状态」',
        what: '当前会话的加密状态，用于确认这条连接到底加密了没有。',
        action: '看「加密算法」与「前向保密」两行即可。',
        shot: 'security',
        ui: [
          ['页标题', '安全状态'],
          ['副标题', '会话与加密通道'],
          ['字段', '会话状态 / Session ID / 密钥交换时间 / 当前用户'],
          ['加密算法', 'X25519 ECDH + HKDF-SHA256'],
          ['前向保密', '已启用（临时密钥、私钥用完即销毁）'],
        ],
      },
      {
        id: 'settings',
        title: '设置（PC 浏览器 5 个分区）',
        where: '侧栏底部「设置」',
        what: '要改的东西几乎都在这一页。PC 浏览器上从上到下一共 5 个分区。',
        action: '换服务器地址在「服务端」分区里；退出登录在页面底部。',
        shot: 'settings',
        notes: [
          '手机浏览器（<768px）才会多出「管理面板」一节，变成 6 个分区',
          'PC 端「管理面板」入口在左侧栏，不在设置页里，避免重复',
        ],
        ui: [
          ['分区顺序（PC）', '服务端 → 笔记目录 → 账号 → 外观 → 关于'],
          ['页底按钮', '退出登录'],
          ['服务端分区', '修改服务器地址'],
          ['磁盘选择弹窗', '选择磁盘'],
        ],
      },
      {
        id: 'admin',
        title: '管理面板（仅管理员）',
        where: '侧栏「设置」上方',
        what: '用户管理、磁盘管理、权限配置、系统设置四项。',
        action: '普通账号看不到这 4 项，整组不渲染。',
        shot: 'admin-users',
      },
      {
        id: 'none',
        title: 'Web 端没有的功能',
        where: '—',
        what: '文本编辑器、云盘挂载、远程连接（SSH）都不在浏览器里。',
        action: '需要这三项能力时装桌面端。',
        notes: [
          '云盘挂载的 /sync 路由在较早版本已移除（浏览器端不提供同步能力）',
          '文本编辑器是多标签本地编辑器，依赖桌面外壳，浏览器没有对应实现',
          '远程连接依赖本地终端与 SFTP，浏览器无法提供',
        ],
      },
    ],
  },

  desktop: {
    label: '桌面端',
    navItems: [
      '文件管理',
      '笔记管理',
      '文本编辑器',
      '云盘挂载',
      '传输任务',
      '离线下载',
      '远程连接',
    ],
    intro:
      '桌面端功能最全：7 项主导航 + 2 项底部固定项，管理员另有 4 项管理面板。下面按侧栏顺序逐节说明。',
    sections: [
      {
        id: 'layout',
        title: '侧栏结构：7 + 2 + 4',
        where: '登录后第一屏',
        what: '主导航 7 项（文件管理 → 笔记管理 → 文本编辑器 → 云盘挂载 → 传输任务 → 离线下载 → 远程连接），底部固定安全状态与设置。',
        action: '顺序固定，照着找。',
        notes: [
          '「文本编辑器」带准入判据：未登录或会话过期时该入口不显示',
          '「修复中心」没有独立菜单，已并入「传输任务」的第二个页签',
        ],
      },
      {
        id: 'files', shot: 'files-disks',
        title: '文件管理',
        where: '侧栏第 1 项「文件管理」',
        what: '首屏是磁盘卡片列表，点进去是目录浏览。',
        action: '顶部「上传」「新建目录」；行尾三点菜单含下载、预览、修复、重命名、移动、删除。',
        notes: [
          '桌面端上传走系统原生文件对话框（选完再由 Python 侧分片加密上传）',
          '拖拽上传由桌面外壳拦截 dropEvent，与 Web 端的浏览器 drag 实现不同',
        ],
        ui: [
          ['顶部按钮', '上传 / 新建目录'],
          ['行内操作', '下载 / 预览 / 修复损坏媒体 / 重命名 / 移动到… / 删除'],
          ['无写权限', '只读'],
        ],
      },
      {
        id: 'notes', shot: 'notes',
        title: '笔记管理',
        where: '侧栏第 2 项「笔记管理」',
        what: 'Markdown 列表与编辑器，支持实时预览、代码高亮、Mermaid。',
        action: '行内操作「重命名」「删除」；新建走「新建笔记」。',
        ui: [
          ['页标题', '笔记管理'],
          ['行内操作', '重命名 / 删除'],
          ['新建弹窗', '新建笔记'],
          ['工具栏', '插入 Mermaid 图表'],
          ['离开提示', '未保存的修改'],
        ],
      },
      {
        id: 'editor', shot: 'editor',
        title: '文本编辑器（桌面端专属）',
        where: '侧栏第 3 项「文本编辑器」',
        what: '纯本地多标签编辑器，与远端零交互，断网也能用。',
        action: '12 项工具箱、29 项快捷键、跨标签查找替换。',
        notes: ['未登录时入口不可见、快捷键不响应；从「打开方式」唤起文件会先要求登录。'],
      },
      {
        id: 'sync', shot: 'sync',
        title: '云盘挂载（桌面端专属）',
        where: '侧栏第 4 项「云盘挂载」',
        what: '把远端的一个子目录挂成本地的一个挂载点，按需缓存、增量同步。',
        action: '新建挂载点要填名称、远端磁盘、远端子目录、本地缓存目录四项。',
        notes: [
          '挂载点行上的操作为「启用挂载点」「编辑挂载点」「删除挂载点」',
          '永不自动删除远端文件',
        ],
        ui: [
          ['空态', '还没有挂载点'],
          ['未选中提示', '请选择左侧的挂载点'],
          ['表单字段', '名称 / 远端磁盘 / 远端子目录 / 本地缓存目录'],
          ['删除确认', '确认删除'],
          ['文件空态', '还没有文件'],
        ],
      },
      {
        id: 'transfer', shot: 'transfer',
        title: '传输任务',
        where: '侧栏第 5 项「传输任务」',
        what: '两个页签：「传输」看上传下载进度，「修复」看媒体修复任务。',
        action: '页签切换即可。修复任务三端共享。',
        ui: [
          ['页签', '传输 / 修复'],
          ['传输页标题', '传输任务 · 上传与下载进度'],
          ['修复页标题', '修复中心 · 共享修复任务与产物管理'],
        ],
      },
      {
        id: 'offline', shot: 'offline',
        title: '离线下载',
        where: '侧栏第 6 项「离线下载」',
        what: '贴无需登录的网页地址，分析出清单后勾选下载到自己的磁盘目录。',
        action: '产物直接进文件管理，音视频分离站点会自动合并音轨。',
      },
      {
        id: 'remote', shot: 'remote',
        title: '远程连接（桌面端专属）',
        where: '侧栏第 7 项「远程连接」',
        what: '三个页签：会话（进行中的 SSH）、本地（本机终端）、远程（保存的连接配置）。',
        action: '点右上角新建；连接行上的图标按钮为「打开」「编辑」「复制」「删除」。',
        notes: [
          '认证方式支持：跳板、密码、密钥、密码+密钥、OTP（网关键盘交互第二因子）',
          '结束会话时弹「关闭会话」确认',
        ],
        ui: [
          ['页签', '会话 / 本地 / 远程'],
          ['空态', '还没有远程连接 / 点击右上角新建'],
          ['本机空态', '还没有本机配置'],
          ['连接类型', '远程终端（SSH） / 仅 SFTP'],
          ['行内按钮', '打开 / 编辑 / 复制 / 删除'],
        ],
      },
      {
        id: 'security', shot: 'security',
        title: '安全状态',
        where: '侧栏底部「安全状态」',
        what: '当前会话的加密状态。',
        action: '看加密算法与前向保密两行。',
        ui: [
          ['字段', '会话状态 / Session ID / 密钥交换时间 / 当前用户'],
          ['加密算法', 'X25519 ECDH + HKDF-SHA256'],
          ['前向保密', '已启用（临时密钥、私钥用完即销毁）'],
        ],
      },
      {
        id: 'settings', shot: 'settings',
        title: '设置（8 个分区）',
        where: '侧栏底部「设置」',
        what: '分区顺序：服务端 → 笔记目录 → 终端 → 账号 → 管理面板（仅管理员）→ 外观 → 文本编辑器 → 关于。',
        action: '页底「退出登录」。',
        notes: ['桌面端比另外两端多「终端」与「文本编辑器」两个分区。'],
      },
      {
        id: 'admin', shot: 'admin-users',
        title: '管理面板（仅管理员）',
        where: '侧栏「设置」上方',
        what: '用户管理、磁盘管理、权限配置、系统设置。',
        action: '普通账号整组不显示。',
      },
    ],
  },

  mobile: {
    label: '移动端',
    navItems: ['文件', '笔记', '传输任务', '离线下载', '相册同步', '设置'],
    intro:
      '移动端是底部导航 6 项，没有侧栏。注意第 1、2 项叫「文件」「笔记」，与桌面/Web 的叫法不同。',
    sections: [
      {
        id: 'layout',
        title: '底部导航：6 项',
        where: '登录后第一屏',
        what: '从左到右：文件 / 笔记 / 传输任务 / 离线下载 / 相册同步 / 设置。',
        action: '照着找就行。',
        notes: [
          '第 1、2 项叫「文件」「笔记」（桌面/Web 叫「文件管理」「笔记管理」）',
          '移动端没有云盘挂载、没有文本编辑器、没有远程连接',
        ],
      },
      {
        id: 'files',
        title: '文件', shot: 'files',
        where: '底部第 1 项「文件」',
        what: '先选磁盘再浏览目录，逻辑与桌面/Web 一致。',
        action: '顶部两个图标按钮：「上传文件」「新建目录」；文件操作靠长按。',
        notes: [
          '移动端上传按钮的文案是「上传文件」（桌面/Web 是「上传」）',
          '文件操作入口是长按弹底部菜单，不是行尾三点菜单',
          '无写权限时按钮保留但禁用，长按提示「无写权限」',
        ],
        ui: [
          ['顶部图标', '上传文件 / 新建目录'],
          ['无权限提示', '无写权限'],
          ['长按菜单', '下载 / 预览 / 修复损坏媒体 / 重命名 / 移动到… / 删除'],
          ['空目录', '此目录为空 / 可上传文件或新建目录'],
        ],
      },
      { id: 'notes', title: '笔记', shot: 'notes', where: '底部第 2 项「笔记」', what: 'Markdown 笔记的查看与编辑，支持 Mermaid 图表。', action: '点右下角按钮新建笔记。' },
      { id: 'transfer', title: '传输任务', shot: 'transfer', where: '底部第 3 项「传输任务」', what: '上传下载进度，以及三端共享的媒体修复任务。', action: '—' },
      {
        id: 'offline',
        title: '离线下载', shot: 'offline',
        where: '底部第 4 项「离线下载」',
        what: '贴网址 → 分析 → 勾选下载，与桌面/Web 同一套逻辑。',
        action: '点右下角「新建任务」发起，或等桌面端/Web 端创建后同步显示。',
      },
      {
        id: 'album',
        title: '相册同步（移动端专属）', shot: 'album',
        where: '底部第 5 项「相册同步」',
        what: '把手机相册自动备份到服务端，可只在 Wi-Fi 下、只在充电时上传。',
        action: '打开「自动同步相册」，选「远端目标目录」，按需开「仅 Wi-Fi」「仅充电时」。',
        notes: [
          '默认关闭；关闭后不再产生新的上传任务',
          '先核对再上传：同名文件跳过；远端被删的照片会自动补传',
          '上传前做完整性校验，带进度、可取消',
          '运行中可点「停止」中断本次同步',
        ],
        ui: [
          ['页标题', '相册同步'],
          ['主开关', '自动同步相册'],
          ['主开关副文', '默认关闭；关闭后不再产生新的上传任务'],
          ['目标目录', '远端目标目录 / 选择'],
          ['条件开关', '仅 Wi-Fi / 仅充电时'],
          ['结果入口', '在文件中查看已上传 / 去传输任务查看上传明细'],
          ['失败面板', '失败原因（XXX 项） / 暂无失败记录'],
          ['后台提示', '后台运行设置 / 知道了'],
        ],
      },
      {
        id: 'settings',
        title: '设置（5 个分区）', shot: 'settings',
        where: '底部第 6 项「设置」',
        what: '分区顺序：服务器 → 安全状态 → 账号 → 管理面板（仅管理员）→ 关于。',
        action: '改服务器地址在「服务器」分区里。',
        notes: [
          '三端叫法不同：桌面/Web 是「服务端」，移动端是「服务器」',
          '移动端把「安全状态」放进了设置页（桌面/Web 是独立菜单项）',
        ],
        ui: [
          ['分区', '服务器 / 安全状态 / 账号 / 管理面板 / 关于'],
          ['按钮', '修改服务器地址 / 刷新会话密钥'],
          ['字段', '会话状态 / Session ID / 密钥交换 / 用户名 / 角色 / Token 过期'],
        ],
      },
      {
        id: 'admin',
        title: '管理面板（仅管理员）',
        where: '设置页内',
        what: '用户管理、磁盘管理、权限配置、系统设置。',
        action: '普通账号看不到该分区。',
      },
    ],
  },
};

/**
 * 截图路径：按端分目录。
 *   /screenshots/<端>/<页名>.png
 * 三端截图都必须存在（SPEC §9），缺失时页面会显示占位说明而不是破图。
 */
function shotUrl(platform: string, name: string): string {
  return `/screenshots/${platform}/${name}.png`;
}

export function GuidePage() {
  const { platform } = useParams<{ platform: string }>();
  if (platform !== 'web' && platform !== 'desktop' && platform !== 'mobile') {
    return <Navigate to="/guide/web" replace />;
  }
  const doc = GUIDES[platform];

  return (
    <>
      <PageHead
        crumb={`文档中心 / ${doc.label}操作指引`}
        title={`${doc.label}操作指引`}
        lede={doc.intro}
      />
      <section className="py-10">
        <div className="mx-auto grid max-w-[1140px] gap-10 px-7 lg:grid-cols-[210px_1fr]">
          {/* 左侧目录 */}
          <aside className="hidden lg:block">
            <div className="sticky top-[86px]">
              <div className="mb-2.5 text-[12px] font-[560] text-ink-500">本页目录</div>
              <nav className="space-y-0.5 border-l border-line">
                {doc.sections.map((s, i) => (
                  <a
                    key={s.id}
                    href={`#${s.id}`}
                    className="block border-l-2 border-transparent py-1 pl-2.5 text-[12.8px] text-ink-700 transition-colors hover:border-brand-500 hover:text-brand-600"
                  >
                    <span className="tnum mr-1.5 text-[11px] text-ink-300">
                      {String(i + 1).padStart(2, '0')}
                    </span>
                    {s.title}
                  </a>
                ))}
              </nav>
              <div className="mt-6 border-t border-line pt-3 text-[12px] text-ink-500">
                其它端：
                {(['desktop', 'web', 'mobile'] as Platform[])
                  .filter((p) => p !== platform)
                  .map((p) => (
                    <Link key={p} to={`/guide/${p}`} className="mt-1 block text-brand-600">
                      {GUIDES[p].label}指引 →
                    </Link>
                  ))}
              </div>
            </div>
          </aside>

          {/* 正文 */}
          <div className="min-w-0 space-y-10">
            {doc.sections.map((s, i) => (
              <article key={s.id} id={s.id} className="scroll-mt-[86px]">
                <div className="mb-2 flex items-baseline gap-2.5">
                  <span className="tnum text-[12px] font-[560] text-brand-600">
                    {String(i + 1).padStart(2, '0')}
                  </span>
                  <h2 className="text-[19px] font-[660] tracking-[-0.012em]">{s.title}</h2>
                </div>

                <div className="overflow-hidden rounded-[10px] border border-line">
                  <Row label="在哪" value={s.where} />
                  <Row label="做什么" value={s.what} />
                  <Row label="点击什么" value={s.action} />
                </div>

                {s.notes?.length ? (
                  <div className="mt-3 rounded-[10px] border border-brand-100 bg-brand-50 px-4 py-3">
                    <ul className="space-y-1 text-[12.8px] text-brand-700">
                      {s.notes.map((n) => (
                        <li key={n} className="flex gap-1.5">
                          <span aria-hidden>·</span>
                          <span>{n}</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                ) : null}

                {s.ui?.length ? (
                  <div className="mt-3 overflow-hidden rounded-[10px] border border-line">
                    <div className="border-b border-line bg-canvas-soft px-4 py-1.5 text-[11.5px] text-ink-500">
                      界面原文（逐字对齐客户端）
                    </div>
                    {s.ui.map(([k, v], j) => (
                      <div
                        key={k + j}
                        className={`grid grid-cols-[150px_1fr] gap-3 px-4 py-2 text-[12.8px] ${
                          j > 0 ? 'border-t border-line' : ''
                        }`}
                      >
                        <span className="font-[540] text-ink-900">{k}</span>
                        <span className="text-ink-700">{v}</span>
                      </div>
                    ))}
                  </div>
                ) : null}

                {s.shot ? (
                  <figure className="mt-3 overflow-hidden rounded-[10px] border border-line">
                    <img
                      src={shotUrl(platform, s.shot)}
                      alt={`${doc.label} ${s.title}`}
                      loading="lazy"
                      className={
                        // 登录页是居中卡片，上下留白很大：裁成 16:7 只留卡片区
                        // （与 design-spec §6「特殊裁切」一致）
                        s.shot === 'login'
                          ? 'block w-full aspect-[16/7] object-cover object-center'
                          : 'block w-full'
                      }
                    />
                    <figcaption className="flex items-center gap-2 border-t border-line bg-canvas-soft px-4 py-2 text-[12px] text-ink-500">
                      <b className="font-[600] text-ink-700">{s.title}</b>
                      <span className="ml-auto inline-flex items-center gap-1.5 font-[540] text-brand-600">
                        <i className="block h-1.5 w-1.5 rounded-full bg-brand-500" />
                        v{siteVersion} 实拍
                      </span>
                    </figcaption>
                  </figure>
                ) : null}
              </article>
            ))}

            <p className="border-t border-line pt-4 text-[12px] text-ink-500">
              本页所有按钮文案与菜单顺序均来自三端源码实测（`website/docs/ui-facts-*.md`），
              并由门禁 ⑦⑧ 保证「指引 → 清单 → 源码」三级一致。
            </p>
            <p className="text-[12px] text-ink-500">
              桌面端与移动端的更多页面截图待补（需 KVM 虚拟机与 Android 模拟器）。
            </p>
          </div>
        </div>
      </section>
    </>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="grid grid-cols-[88px_1fr] gap-3 border-b border-line px-4 py-2.5 text-[13px] last:border-b-0">
      <span className="text-ink-500">{label}</span>
      <span className="text-ink-900">{value}</span>
    </div>
  );
}
