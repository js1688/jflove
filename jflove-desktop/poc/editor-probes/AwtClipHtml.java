import java.awt.GraphicsEnvironment;
import java.awt.Toolkit;
import java.awt.datatransfer.Clipboard;
import java.awt.datatransfer.ClipboardOwner;
import java.awt.datatransfer.DataFlavor;
import java.awt.datatransfer.Transferable;
import java.awt.datatransfer.UnsupportedFlavorException;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.atomic.AtomicLong;

/**
 * AwtClipHtml —— 用 JDK 自己的 AWT 公开 API 把 {@code text/html} 与 {@code text/plain}
 * 两个 flavor 放进**系统剪贴板**（jflove v1.6.1 · 用户反馈 F6 的 r18 真机取证工具）。
 *
 * <h2>为什么要写这个程序</h2>
 * 用户反馈"IDEA 里复制的代码贴不进本应用编辑器"。IDEA 是 Java 应用，它把 HTML 放上剪贴板
 * 走的就是 JDK 的 AWT 通路（{@code java.awt.Toolkit.getDefaultToolkit().getSystemClipboard()}）。
 * 以前各轮只能用 Python **手搓**"AWT 风格"的 CF_HTML 近似载荷 ⇒ 结论停在"机制证实、真凶未定"。
 * 本程序的作用是：让 **JDK 自己**产生"真实 AWT 载荷"，再喂给 r17 的粘贴判据 ⇒ 判定真凶。
 *
 * <h2>三种 html flavor 形态（对应 JDK 21 的三条分支）</h2>
 * {@code sun.awt.windows.WDataTransferer.translateTransferable} 对 {@code CF_HTML} 的处理是：
 * <ol>
 *   <li>Transferable 提供 {@code DataFlavor.selectionHtmlFlavor}
 *       （{@code text/html;class=java.lang.String;document=selection}）⇒ **原样**写入，
 *       调用方自己负责 CF_HTML 头；</li>
 *   <li>否则提供 {@code DataFlavor.allHtmlFlavor}（{@code …;document=all}）⇒ **原样**写入，
 *       同样**不加头**；</li>
 *   <li>否则（自定义 flavor / {@code …;document=fragment}）⇒ 交给
 *       {@code HTMLCodec.convertToHTMLFormat} 包装：
 *       {@code Version:1.0} + {@code StartHTML}/{@code EndHTML}/{@code StartFragment}/{@code EndFragment}
 *       + {@code SourceURL:} 五个字段（10 位补零）+ {@code <HTML><BODY>} 前后缀，
 *       **不写 {@code <!--StartFragment-->} 注释标记**，偏移按 UTF-8 字节算。</li>
 * </ol>
 * 所以 {@code --flavor=all} 与 {@code --flavor=custom} 会产生**结构完全不同**的真实 AWT 载荷，
 * 两者都要试（可能分别对应 IDEA 与别的 Java 应用）。
 *
 * <h2>为什么设完剪贴板后**必须让 JVM 活着**</h2>
 * AWT 的剪贴板是**延迟渲染**（delayed rendering）的：真实数据由本进程在**读取方索要时**
 * 才通过 {@code Transferable.getTransferData} 生成（Windows 侧由 OLE 数据对象回调）。
 * JVM 一退出，剪贴板内容即失效 ⇒ 读取方（Qt / Blink）拿到空数据，得出**假结论**。
 * 因此本程序设完剪贴板后进入**心跳驻留**循环（默认 900 秒），并把
 * {@code getTransferData} 的**被调用次数**打出来 —— 那就是"数据确实是本进程在响应时生成的"证据。
 * 父脚本跑完粘贴判据后**显式杀掉**本进程。
 *
 * <h2>用法（JDK 21 单文件源码模式，不落 .class）</h2>
 * <pre>
 *   D:\jdk\jdk-21.0.9\bin\java.exe AwtClipHtml.java [选项] [html] [plain]
 *   --flavor=all|custom|fragment|selection   html flavor 形态（默认 all）
 *   --preset=ascii|cjk|doc                   预设载荷（中文不进命令行，避免编码风险）
 *   --hold=SECONDS                           驻留秒数（默认 900）
 *   --no-text                                不写 text/plain（隔离实验）
 *   --text-only                              只写 text/plain（对照：剪贴板上**没有** CF_HTML）
 *   --no-probe                               跳过 AWT 自检（判定"数据是谁、何时渲染的"）
 *   --heartbeat=SECONDS                      心跳间隔（默认 5）
 * </pre>
 *
 * <h2>纪律</h2>
 * 只打印 **长度 / FNV-1a 指纹 / flavor mime / 计数 / 布尔**，**绝不打印载荷正文**（AGENTS.md §9.14）。
 */
public final class AwtClipHtml {

    /** 默认 html 载荷：ASCII 代码片段（位置参数可覆盖）。 */
    private static final String DEFAULT_HTML =
            "<pre>r18-awt-real-ascii-1\nr18-awt-real-ascii-2</pre>";
    /** 默认纯文本载荷（与 html 的**可见文本**一致、但不带标签）。 */
    private static final String DEFAULT_PLAIN =
            "r18-awt-real-ascii-1\nr18-awt-real-ascii-2";

    /**
     * 预设载荷（**定义在源码里** ⇒ 中文不经过命令行，避免任何命令行编码风险）。
     *
     * 三个预设都是有意的：
     * <ul>
     *   <li>{@code ascii} —— 纯 ASCII 片段，偏移口径的**基线**；</li>
     *   <li>{@code cjk} —— 片段含中文 ⇒ 检验 `StartFragment/EndFragment` 是**字节**还是**字符**口径；</li>
     *   <li>{@code doc} —— IDEA 最可能的样子：**完整 HTML 文档**（{@code <html><head><style>} + {@code <pre>}）
     *       且含中文注释。</li>
     * </ul>
     */
    private static final Map<String, String[]> PRESETS = Map.of(
            "ascii", new String[] {"<b>r18-awt-real-ascii</b>", "r18-awt-real-ascii"},
            "cjk", new String[] {"<b>中文注释 r18 中文</b>", "中文注释 r18 中文"},
            "chromium", new String[] {
                "<!--StartFragment--><b>r18-awt-real-marker</b><!--EndFragment-->",
                "r18-awt-real-marker"},
            "doc", new String[] {
                "<html><head><meta charset=\"utf-8\">"
                        + "<style>pre{font-family:monospace}</style></head>"
                        + "<body><pre>r18-awt-real-doc <span>中文注释</span></pre></body></html>",
                "r18-awt-real-doc 中文注释"});

    /** 各 flavor 的 `getTransferData` 被调用次数（延迟渲染的证据）。 */
    private static final Map<String, AtomicLong> SERVED = new LinkedHashMap<>();

    private AwtClipHtml() {
    }

    /** 把字符串编成 UTF-8 字节后算 FNV-1a 32 位（与 Python / 渲染层同口径）。 */
    private static String fnv1a32(String text) {
        byte[] bytes = text.getBytes(java.nio.charset.StandardCharsets.UTF_8);
        int hash = 0x811C9DC5;
        for (byte b : bytes) {
            hash ^= (b & 0xFF);
            hash = (hash * 0x01000193);
        }
        return String.format("%08x", hash);
    }

    private static long countCodePoints(String text) {
        return text.codePointCount(0, text.length());
    }

    /** 参数解析结果。 */
    private static final class Options {
        String flavorMode = "all";
        int holdSeconds = 900;
        int heartbeatSeconds = 5;
        boolean withText = true;
        boolean probeAvailable = true;
        boolean textOnly = false;
        String preset = "";
        String html = DEFAULT_HTML;
        String plain = DEFAULT_PLAIN;
    }

    private static Options parseArgs(String[] args) {
        Options opt = new Options();
        List<String> positional = new ArrayList<>();
        for (String arg : args) {
            if (arg.startsWith("--flavor=")) {
                opt.flavorMode = arg.substring("--flavor=".length()).trim().toLowerCase();
            } else if (arg.startsWith("--hold=")) {
                opt.holdSeconds = Integer.parseInt(arg.substring("--hold=".length()).trim());
            } else if (arg.startsWith("--heartbeat=")) {
                opt.heartbeatSeconds = Integer.parseInt(arg.substring("--heartbeat=".length()).trim());
            } else if (arg.startsWith("--preset=")) {
                opt.preset = arg.substring("--preset=".length()).trim().toLowerCase();
            } else if (arg.equals("--no-text")) {
                opt.withText = false;
            } else if (arg.equals("--no-probe")) {
                // 跳过 `getAvailableDataFlavors()` 自检：用于判定"数据是**谁**在什么时候渲染的"
                opt.probeAvailable = false;
            } else if (arg.equals("--text-only")) {
                // 对照实验：只放 `DataFlavor.stringFlavor`（= `StringSelection`），**不放** text/html。
                // 用途：把"CF_HTML 的形态问题"与"外部进程持有剪贴板"这两件事分开判。
                opt.textOnly = true;
            } else if (arg.startsWith("--")) {
                throw new IllegalArgumentException("未知选项：" + arg);
            } else {
                positional.add(arg);
            }
        }
        if (!positional.isEmpty()) {
            opt.html = positional.get(0);
        }
        if (positional.size() > 1) {
            opt.plain = positional.get(1);
        }
        if (!opt.preset.isEmpty()) {
            String[] payload = PRESETS.get(opt.preset);
            if (payload == null) {
                throw new IllegalArgumentException("--preset 取值非法：" + opt.preset
                        + "（合法：" + PRESETS.keySet() + "）");
            }
            if (positional.isEmpty()) {
                opt.html = payload[0];
            }
            if (positional.size() < 2) {
                opt.plain = payload[1];
            }
        }
        return opt;
    }

    /**
     * 按 `--flavor` 造出要放进剪贴板的 html {@link DataFlavor}。
     *
     * `custom` 用 {@code new DataFlavor("text/html;class=java.lang.String;charset=UTF-8")}
     * —— 它既不是 selectionHtmlFlavor 也不是 allHtmlFlavor，因此会走
     * {@code HTMLCodec.convertToHTMLFormat} 那条**带 CF_HTML 头**的分支。
     */
    private static DataFlavor htmlFlavorFor(String mode) throws ClassNotFoundException {
        switch (mode) {
            case "all":
                return DataFlavor.allHtmlFlavor;
            case "selection":
                return DataFlavor.selectionHtmlFlavor;
            case "fragment":
                return DataFlavor.fragmentHtmlFlavor;
            case "custom":
                return new DataFlavor("text/html;class=java.lang.String;charset=UTF-8");
            default:
                throw new IllegalArgumentException("--flavor 取值非法：" + mode
                        + "（合法：all / custom / fragment / selection）");
        }
    }

    /** 只放 html（+ 可选纯文本）的 Transferable；`getTransferData` 计数就是延迟渲染证据。 */
    private static final class HtmlTransferable implements Transferable {
        private final DataFlavor htmlFlavor;
        private final String html;
        private final String plain;
        private final boolean withText;

        HtmlTransferable(DataFlavor htmlFlavor, String html, String plain, boolean withText) {
            this.htmlFlavor = htmlFlavor;
            this.html = html;
            this.plain = plain;
            this.withText = withText;
            SERVED.put(htmlFlavor.getMimeType(), new AtomicLong());
            if (withText) {
                SERVED.put(DataFlavor.stringFlavor.getMimeType(), new AtomicLong());
            }
        }

        @Override
        public DataFlavor[] getTransferDataFlavors() {
            if (withText) {
                return new DataFlavor[] {htmlFlavor, DataFlavor.stringFlavor};
            }
            return new DataFlavor[] {htmlFlavor};
        }

        @Override
        public boolean isDataFlavorSupported(DataFlavor flavor) {
            return htmlFlavor.equals(flavor)
                    || (withText && DataFlavor.stringFlavor.equals(flavor));
        }

        @Override
        public Object getTransferData(DataFlavor flavor) throws UnsupportedFlavorException {
            if (htmlFlavor.equals(flavor)) {
                bump(htmlFlavor);
                return html;
            }
            if (withText && DataFlavor.stringFlavor.equals(flavor)) {
                bump(DataFlavor.stringFlavor);
                return plain;
            }
            throw new UnsupportedFlavorException(flavor);
        }

        private void bump(DataFlavor flavor) {
            AtomicLong counter = SERVED.get(flavor.getMimeType());
            if (counter != null) {
                counter.incrementAndGet();
            }
        }
    }

    private static String servedSnapshot() {
        StringBuilder sb = new StringBuilder();
        for (Map.Entry<String, AtomicLong> entry : SERVED.entrySet()) {
            if (sb.length() > 0) {
                sb.append(',');
            }
            sb.append('"').append(entry.getKey()).append("\":").append(entry.getValue().get());
        }
        return "{" + sb + "}";
    }

    /** 起一条心跳行（不打印任何正文，只有计数）。 */
    private static void heartbeat(int elapsedSeconds, String phase) {
        System.out.println("[hb] t=" + elapsedSeconds + "s phase=" + phase
                + " served=" + servedSnapshot());
        System.out.flush();
    }

    public static void main(String[] args) throws Exception {
        // 控制台编码（GBK）会把日志里的中文变成乱码；统一按 UTF-8 输出，便于证据归档
        System.setOut(new java.io.PrintStream(
                new java.io.FileOutputStream(java.io.FileDescriptor.out), true,
                java.nio.charset.StandardCharsets.UTF_8));

        Options opt;
        try {
            opt = parseArgs(args);
        } catch (RuntimeException exc) {
            System.out.println("[fatal] " + exc.getMessage());
            System.out.flush();
            System.exit(2);
            return;
        }

        System.out.println("[env] java=" + System.getProperty("java.version")
                + " os=" + System.getProperty("os.name")
                + " headlessProperty=" + System.getProperty("java.awt.headless"));
        System.out.println("[env] graphicsEnvironmentIsHeadless="
                + GraphicsEnvironment.isHeadless());
        System.out.println("[payload] flavorMode=" + opt.flavorMode
                + " withText=" + opt.withText
                + " htmlCodePoints=" + countCodePoints(opt.html)
                + " htmlUtf8Bytes=" + opt.html.getBytes(java.nio.charset.StandardCharsets.UTF_8).length
                + " htmlFnv1a32=" + fnv1a32(opt.html)
                + " plainCodePoints=" + countCodePoints(opt.plain)
                + " plainUtf8Bytes=" + opt.plain.getBytes(java.nio.charset.StandardCharsets.UTF_8).length
                + " plainFnv1a32=" + fnv1a32(opt.plain));
        System.out.flush();

        if (GraphicsEnvironment.isHeadless()) {
            System.out.println("[fatal] headless=true ⇒ AWT 剪贴板不可用（需要真实桌面会话）");
            System.out.flush();
            System.exit(3);
            return;
        }

        DataFlavor htmlFlavor;
        try {
            htmlFlavor = htmlFlavorFor(opt.flavorMode);
        } catch (RuntimeException | ClassNotFoundException exc) {
            System.out.println("[fatal] 构造 html flavor 失败：" + exc);
            System.out.flush();
            System.exit(2);
            return;
        }
        System.out.println("[flavor] html=" + htmlFlavor.getMimeType()
                + " isSelectionHtml=" + htmlFlavor.equals(DataFlavor.selectionHtmlFlavor)
                + " isAllHtml=" + htmlFlavor.equals(DataFlavor.allHtmlFlavor)
                + " textUtilsFlavor=" + htmlFlavor.isFlavorTextType());
        System.out.flush();

        Clipboard clipboard = Toolkit.getDefaultToolkit().getSystemClipboard();
        Transferable transferable;
        if (opt.textOnly) {
            System.out.println("[text-only] 只放 DataFlavor.stringFlavor（StringSelection），不放 text/html");
            System.out.flush();
            SERVED.put(DataFlavor.stringFlavor.getMimeType(), new AtomicLong());
            transferable = new java.awt.datatransfer.StringSelection(opt.plain);
        } else {
            transferable = new HtmlTransferable(htmlFlavor, opt.html, opt.plain, opt.withText);
        }
        ClipboardOwner owner = (clip, contents) ->
                System.out.println("[owner] lostOwnership（剪贴板所有权易主）");

        long startedAt = System.currentTimeMillis();
        boolean setOk = false;
        String setError = "";
        try {
            clipboard.setContents(transferable, owner);
            setOk = true;
        } catch (RuntimeException | Error exc) {
            setError = exc.getClass().getSimpleName() + ": " + exc.getMessage();
        }
        System.out.println("[set] setContentsOk=" + setOk
                + " error=" + (setError.isEmpty() ? "none" : setError)
                + " elapsedMs=" + (System.currentTimeMillis() - startedAt));
        System.out.flush();
        if (!setOk) {
            System.exit(4);
            return;
        }

        // 回读**可用 flavor 清单**（只读、只看 mime，不看内容）⇒ 自证"HTML Format 真的在剪贴板上"
        Thread.sleep(300);
        if (!opt.probeAvailable) {
            System.out.println("[available] 自检已跳过（--no-probe）");
            System.out.flush();
        } else {
            try {
                DataFlavor[] available = clipboard.getAvailableDataFlavors();
                StringBuilder sb = new StringBuilder();
                for (DataFlavor flavor : available) {
                    if (sb.length() > 0) {
                        sb.append(" | ");
                    }
                    sb.append(flavor.getMimeType());
                }
                System.out.println("[available] count=" + available.length + " flavors=" + sb);
                System.out.println("[available] htmlAvailable="
                        + clipboard.isDataFlavorAvailable(htmlFlavor)
                        + " stringAvailable="
                        + clipboard.isDataFlavorAvailable(DataFlavor.stringFlavor));
            } catch (IllegalStateException exc) {
                System.out.println("[available] 读取失败：" + exc);
            }
            System.out.flush();
        }

        System.out.println("[ready] 驻留 " + opt.holdSeconds + " 秒（延迟渲染：JVM 必须活着，"
                + "否则读取方拿到空数据 ⇒ 假结论）");
        System.out.flush();

        int waited = 0;
        while (waited < opt.holdSeconds) {
            int step = Math.min(opt.heartbeatSeconds, opt.holdSeconds - waited);
            Thread.sleep(step * 1000L);
            waited += step;
            heartbeat(waited, "holding");
        }
        System.out.println("[done] 驻留超时结束；最终 served=" + servedSnapshot());
        System.out.flush();
    }
}
