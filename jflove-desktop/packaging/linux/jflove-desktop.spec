# JFLove 桌面端 Fedora RPM spec
#
# 载荷是 build_rpm.sh 预先用 PyInstaller 打好的 onedir 产物（SOURCES/JFLove/）：
# 本 spec 只负责 FHS 布局 + 桌面集成 + 依赖声明，**不做源码编译**。
#
# 版本号由 build_rpm.sh 通过 --define "jflove_version <ver>" 传入，
# 唯一真相是仓库根 version.json（本文件不写死版本号）。
#
# 手动调用（可选）：
#   rpmbuild --define "_topdir <dir>" --define "jflove_version 1.5.0" -bb jflove-desktop.spec

# 版本号：未传入时给一个明显无效的占位值，避免"看起来像成功"
%{!?jflove_version:%global jflove_version 0.0.0}
# %{?dist} 缺失时（非 Fedora 主机）给一个默认值，保证产物名仍是 -1.fc43 形态
%{!?dist:%global dist .fc43}

# 冻结包（PyInstaller onedir）本身已是成品，跳过发行版安装后处理，原因：
#   1) strip / find-debuginfo 会去拆 bundle 内自带 .so 的调试信息（667MB 载荷会极慢，
#      且有破坏 bundle 内部依赖的风险）；
#   2) brp-check-rpaths / check-buildroot 面向"从源码链接系统库"的常规包，
#      对 PyInstaller 冻结包会产生 $ORIGIN 相对 rpath、构建路径残留之类的误报。
# 若将来改为从源码构建，请删掉这一行以恢复发行版检查。
%global __os_install_post %{nil}

# FHS 布局（RPM 产物的硬规则见 `AGENTS.md` §8.2；构建/验收留档见
#           `文档记录/桌面端开发记录/v1.5.0.md` §4.5「Linux 环境验收（用户侧）」）
%global jflove_libdir %{_prefix}/lib/jflove
%global jflove_docdir %{_docdir}/jflove-desktop
%global jflove_payload %{_sourcedir}/JFLove

Name:           jflove-desktop
Version:        %{jflove_version}
Release:        1%{?dist}
Summary:        JFLove 跨平台文件同步与笔记客户端
License:        MIT
URL:            https://github.com/js1688/jflove
BuildArch:      x86_64

# PyInstaller 自带的 .so 会被 rpmbuild 自动扫描出大量虚假依赖
# （如 libpython3.14.so.1.0 这类只在 bundle 内部存在的库），必须关掉自动
# 依赖生成，改为下面显式声明运行期最低需求。
AutoReqProv:    no

# Qt 6 + QtWebEngine 运行期最低需求（见计划 §4.2）
Requires:       glibc
Requires:       libGL
Requires:       libxkbcommon
Requires:       fontconfig
Requires:       nss
Requires:       libXcomposite
Requires:       libXdamage
Requires:       libXrandr
Requires:       libXcursor
Requires:       mesa-libEGL

%description
JFLove 桌面客户端（端到端加密的文件同步与笔记应用）。

本包为 Fedora / RHEL 系 RPM：载荷是 PyInstaller 的 onedir 产物，安装在
/usr/lib/jflove/，通过 /usr/bin/jflove 启动，并注册应用菜单项与图标。
用户数据（配置 / 登录会话 / 日志 / 本地存储）存放在 ~/.local/share/JFLove，
与安装目录完全分离。

%prep
# 载荷为预构建的 onedir 目录（%{_sourcedir}/JFLove），无需解包源码

%build
# 无需编译：PyInstaller 已在 build_rpm.sh 中完成

%install
rm -rf %{buildroot}
if [ ! -d %{jflove_payload} ]; then
  echo "缺少载荷目录 %{jflove_payload}（请通过 packaging/linux/build_rpm.sh 构建）" >&2
  exit 1
fi

# 1) onedir 载荷 → /usr/lib/jflove/
mkdir -p %{buildroot}%{jflove_libdir}
cp -a %{jflove_payload}/. %{buildroot}%{jflove_libdir}/

# 2) 启动包装脚本 → /usr/bin/jflove
mkdir -p %{buildroot}%{_bindir}
install -m 0755 %{_sourcedir}/jflove.wrapper %{buildroot}%{_bindir}/jflove

# 3) 菜单项 + 图标 + AppStream 元数据
mkdir -p %{buildroot}%{_datadir}/applications
install -m 0644 %{_sourcedir}/jflove.desktop %{buildroot}%{_datadir}/applications/jflove.desktop
mkdir -p %{buildroot}%{_datadir}/icons/hicolor/256x256/apps
install -m 0644 %{_sourcedir}/jflove-256.png \
        %{buildroot}%{_datadir}/icons/hicolor/256x256/apps/jflove.png
mkdir -p %{buildroot}%{_datadir}/metainfo
install -m 0644 %{_sourcedir}/jflove.metainfo.xml \
        %{buildroot}%{_datadir}/metainfo/jflove.metainfo.xml

# 4) 文件关联 MIME 包（设计 v1.6.0 §6.7.3 / AC-43）
#    jflove.xml 是**生成物**：由 scripts/gen_file_assoc_assets.py 从
#    ui/public/file-assoc.manifest.json（← scratch-types.ts 的 TYPES）派生，
#    这里只登记安装位置；扩展名清单不手写（§5.1 反例禁止）。
#    装完后由 %post / %postun 的 update-mime-database 登记/注销。
mkdir -p %{buildroot}%{_datadir}/mime/packages
install -m 0644 %{_sourcedir}/jflove.xml %{buildroot}%{_datadir}/mime/packages/jflove.xml

# 5) 文档
mkdir -p %{buildroot}%{jflove_docdir}
install -m 0644 %{_sourcedir}/README.md %{buildroot}%{jflove_docdir}/README.md
install -m 0644 %{_sourcedir}/LICENSE %{buildroot}%{jflove_docdir}/LICENSE

%files
%{_bindir}/jflove
%{jflove_libdir}
%{_datadir}/applications/jflove.desktop
# 文件关联 MIME 包：**必须**在 %files 登记，否则 rpmbuild 以
# "Installed (but unpackaged) file(s) found" 失败（设计 §6.7.3「三处登记，缺一即失败」）
%{_datadir}/mime/packages/jflove.xml
%{_datadir}/icons/hicolor/256x256/apps/jflove.png
%{_datadir}/metainfo/jflove.metainfo.xml
%{jflove_docdir}/README.md
%{jflove_docdir}/LICENSE

%post
# 末尾的 `|| :` 不可省略：最小化 Fedora 上这些命令可能不存在，
# 不能因为它们缺失就让安装失败。
# update-mime-database 把 jflove.xml 的 glob 编进 MIME 数据库（JFLove 自有类型
# application/x-jflove-text）；**不调** xdg-mime default、不写 mimeapps.list（不改默认程序）。
update-mime-database %{_datadir}/mime &> /dev/null || :
update-desktop-database &> /dev/null || :
gtk-update-icon-cache -q -t -f %{_datadir}/icons/hicolor &> /dev/null || :

%postun
# 卸载同样重建 MIME 数据库（同 %post 的理由：命令可能不存在，不能因此失败）
update-mime-database %{_datadir}/mime &> /dev/null || :
update-desktop-database &> /dev/null || :
gtk-update-icon-cache -q -t -f %{_datadir}/icons/hicolor &> /dev/null || :

%changelog
* Wed Oct 14 2026 js1688 <js1688@users.noreply.github.com> - %{version}-1
- 新增文本编辑器（v1.6.0）：登记文件关联 MIME 包 jflove.xml
  （application/x-jflove-text + 按 manifest 派生的 glob）与 .desktop 的 MimeType 行
* Mon Sep 14 2026 js1688 <js1688@users.noreply.github.com> - %{version}-1
- 首个 Fedora RPM 包：PyInstaller onedir 载荷 + FHS 布局 + 桌面集成
