/* ============================================================================
   P0-1 PoC：Web 自绘标题栏 ↔ Python 窗口控制
   ----------------------------------------------------------------------------
   为什么不能像 Electron 那样用 CSS 搞定：
     `-webkit-app-region: drag` 是 Electron 私有扩展，QtWebEngine 不支持。
     因此拖拽与缩放必须由 JS 判定意图后，经 QWebChannel 通知 Python 调用
     QWindow.startSystemMove() / startSystemResize()，把控制权交给操作系统。
   ========================================================================= */

(function () {
  'use strict';

  /** 边缘缩放热区宽度（px）—— 必须与 main.py 的 RESIZE_MARGIN 保持一致 */
  var RESIZE_MARGIN = 6;

  /** Qt::Edges 位掩码 */
  var EDGE = { left: 1, top: 2, right: 4, bottom: 8 };

  /** 双击判定窗口（ms）与位移容差（px） */
  var DOUBLE_CLICK_MS = 400;
  var DOUBLE_CLICK_SLOP = 6;

  var ICON_MAX =
    '<svg width="10" height="10" viewBox="0 0 10 10" aria-hidden="true">' +
    '<rect x="0.5" y="0.5" width="9" height="9" fill="none" stroke="currentColor"/>' +
    '</svg>';

  var ICON_RESTORE =
    '<svg width="10" height="10" viewBox="0 0 10 10" aria-hidden="true">' +
    '<rect x="0.5" y="2.5" width="7" height="7" fill="none" stroke="currentColor"/>' +
    '<path d="M2.5 2.5V0.5h7v7h-2" fill="none" stroke="currentColor"/>' +
    '</svg>';

  var bridge = null;
  var maximized = false;

  var lastDownAt = 0;
  var lastDownX = 0;
  var lastDownY = 0;

  var titlebar = document.getElementById('titlebar');
  var btnMin = document.getElementById('btn-min');
  var btnMax = document.getElementById('btn-max');
  var btnClose = document.getElementById('btn-close');
  var dotEl = document.getElementById('bridge-dot');
  var statusEl = document.getElementById('bridge-status');
  var infoEl = document.getElementById('bridge-info');

  /* ---------------------------------------------------------------- 边缘判定 */

  /**
   * 计算某个坐标命中的窗口边缘。
   * @param {number} x 客户端 X
   * @param {number} y 客户端 Y
   * @returns {number} Qt::Edges 位掩码（0 = 不在热区内）
   */
  function edgesAt(x, y) {
    var edges = 0;
    if (x <= RESIZE_MARGIN) {
      edges |= EDGE.left;
    } else if (x >= window.innerWidth - RESIZE_MARGIN) {
      edges |= EDGE.right;
    }
    if (y <= RESIZE_MARGIN) {
      edges |= EDGE.top;
    } else if (y >= window.innerHeight - RESIZE_MARGIN) {
      edges |= EDGE.bottom;
    }
    return edges;
  }

  /**
   * 边缘掩码 → CSS 缩放光标。
   * @param {number} edges Qt::Edges 位掩码
   * @returns {string} CSS cursor 值
   */
  function cursorFor(edges) {
    var l = (edges & EDGE.left) !== 0;
    var r = (edges & EDGE.right) !== 0;
    var t = (edges & EDGE.top) !== 0;
    var b = (edges & EDGE.bottom) !== 0;
    if ((l && t) || (r && b)) return 'nwse-resize';
    if ((r && t) || (l && b)) return 'nesw-resize';
    if (l || r) return 'ew-resize';
    if (t || b) return 'ns-resize';
    return '';
  }

  /* ------------------------------------------------------------ 最大化状态 */

  /**
   * 同步最大化状态到界面（图标与标题）。
   * @param {boolean} value 是否最大化
   */
  function setMaximized(value) {
    maximized = !!value;
    document.body.classList.toggle('is-maximized', maximized);
    if (btnMax) {
      btnMax.innerHTML = maximized ? ICON_RESTORE : ICON_MAX;
      var label = maximized ? '还原' : '最大化';
      btnMax.title = label;
      btnMax.setAttribute('aria-label', label);
    }
    if (maximized) {
      document.body.style.cursor = '';
    }
  }

  /**
   * 更新桥状态指示。
   * @param {string} text 状态文本
   * @param {string} kind ok | warn | err
   */
  function setStatus(text, kind) {
    if (statusEl) statusEl.textContent = text;
    if (dotEl) {
      dotEl.className = 'dot' + (kind ? ' is-' + kind : '');
    }
  }

  /* ------------------------------------------------------------ 交互绑定 */

  document.addEventListener('mousemove', function (e) {
    if (maximized) {
      document.body.style.cursor = '';
      return;
    }
    var edges = edgesAt(e.clientX, e.clientY);
    // 仅在热区内覆盖光标，避免影响内容区自身的 cursor 语义
    document.body.style.cursor = edges ? cursorFor(edges) : '';
  });

  document.addEventListener('mouseleave', function () {
    document.body.style.cursor = '';
  });

  document.addEventListener('mousedown', function (e) {
    if (e.button !== 0) {
      return;
    }

    // ① 优先级最高：边缘缩放（最大化状态下不做缩放）
    var edges = maximized ? 0 : edgesAt(e.clientX, e.clientY);
    if (edges) {
      e.preventDefault();
      if (bridge) bridge.startSystemResize(edges);
      return;
    }

    // ② 标题栏空白处：拖拽移动 / 双击最大化
    if (!titlebar || !titlebar.contains(e.target)) {
      return;
    }
    if (e.target.closest && e.target.closest('.win-btn')) {
      return; // 窗口按钮自行处理，不参与拖拽
    }

    var now = Date.now();
    var isDouble =
      now - lastDownAt < DOUBLE_CLICK_MS &&
      Math.abs(e.clientX - lastDownX) < DOUBLE_CLICK_SLOP &&
      Math.abs(e.clientY - lastDownY) < DOUBLE_CLICK_SLOP;

    lastDownAt = isDouble ? 0 : now;
    lastDownX = e.clientX;
    lastDownY = e.clientY;

    if (isDouble) {
      // 双击检测必须自己实现：startSystemMove() 把控制权交给系统后，
      // 浏览器不会再派发 dblclick 事件
      if (bridge) bridge.toggleMaximize();
      return;
    }

    e.preventDefault();
    if (bridge) bridge.startSystemMove();
  });

  if (btnMin) {
    btnMin.addEventListener('click', function () {
      if (bridge) bridge.minimize();
    });
  }
  if (btnMax) {
    btnMax.addEventListener('click', function () {
      if (bridge) bridge.toggleMaximize();
    });
  }
  if (btnClose) {
    btnClose.addEventListener('click', function () {
      if (bridge) bridge.close();
    });
  }

  /* ------------------------------------------------------------ 桥初始化 */

  function boot() {
    setMaximized(false);

    if (typeof QWebChannel === 'undefined' || !window.qt || !qt.webChannelTransport) {
      // 退化路径：直接用浏览器打开本页面时没有桥，只用于看样式
      setStatus('未检测到 QWebChannel（浏览器直开时属正常）', 'warn');
      return;
    }

    new QWebChannel(qt.webChannelTransport, function (channel) {
      bridge = channel.objects.bridge;

      if (!bridge) {
        setStatus('桥对象缺失', 'err');
        return;
      }

      setStatus('桥已连通', 'ok');
      bridge.maximizedChanged.connect(setMaximized);
      bridge.isMaximized(setMaximized);

      // 自检：拉一次平台信息，证明 JS → Python → JS 往返可用
      bridge.platformInfo(function (info) {
        if (infoEl) infoEl.textContent = String(info);
      });
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }

  /* ------------------------------------------------- 自检接口（供无头测试用） */

  window.__JF_POC__ = {
    /** 页面结构 + 桥状态的快照（JSON 字符串） */
    probe: function () {
      var rootStyle = getComputedStyle(document.documentElement);
      return JSON.stringify({
        titlebar: !!titlebar,
        buttons: ['btn-min', 'btn-max', 'btn-close'].filter(function (id) {
          return !!document.getElementById(id);
        }),
        bridgeReady: !!bridge,
        maximized: maximized,
        resizeMargin: RESIZE_MARGIN,
        headerHeight: rootStyle.getPropertyValue('--header-h').trim(),
        sidebarWidth: rootStyle.getPropertyValue('--sidebar-w').trim(),
        viewport: [window.innerWidth, window.innerHeight],
      });
    },

    /** 直接用 browser 打开时无桥，此接口仍可用于验证热区算法 */
    edgesAt: function (x, y) {
      return edgesAt(x, y);
    },

    cursorAt: function (x, y) {
      return cursorFor(edgesAt(x, y));
    },

    /** 手动设置最大化状态（仅用于验证图标切换逻辑，不驱动真实窗口） */
    setMaximized: function (value) {
      setMaximized(value);
      return maximized;
    },

    /**
     * 调用带返回值的桥方法（Promise 解析为 JSON 字符串）。
     * @param {string} name 方法名
     * @param {*} [arg] 单参数
     */
    call: function (name, arg) {
      return new Promise(function (resolve) {
        var fn = bridge ? bridge[name] : null;
        if (typeof fn !== 'function') {
          resolve(JSON.stringify({ ok: false, error: 'method unavailable: ' + name }));
          return;
        }
        var settled = false;
        var done = function (value) {
          if (settled) return;
          settled = true;
          resolve(JSON.stringify({ ok: true, value: value === undefined ? null : value }));
        };
        try {
          if (arg === null || arg === undefined) {
            fn(done);
          } else {
            fn(arg, done);
          }
        } catch (err) {
          resolve(JSON.stringify({ ok: false, error: String(err) }));
        }
      });
    },

    /** 调用无返回值（void）的桥方法 */
    callVoid: function (name, arg) {
      var fn = bridge ? bridge[name] : null;
      if (typeof fn !== 'function') {
        return 'method unavailable: ' + name;
      }
      try {
        if (arg === null || arg === undefined) {
          fn();
        } else {
          fn(arg);
        }
      } catch (err) {
        return 'error: ' + String(err);
      }
      return 'ok';
    },
  };
})();
