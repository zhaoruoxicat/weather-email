# weatheremail deb 打包
#
# 用法（在 packaging/ 目录下）：
#     make            # 组装目录树并生成 deb
#     make clean      # 清理 build 产物
#
# 产物：build/weatheremail_<版本>_all.deb

PKG      := weatheremail
ROOT_SRC := $(abspath $(CURDIR)/..)
DEST     := $(CURDIR)/build/$(PKG)
BUILD    := $(CURDIR)/build

# 版本号单一来源：读包内 __init__.py
# 必须用 := 立即展开，且在 : 展开之前用 override 固定，否则 filenames 里会是空。
VERSION  := $(shell python3 -c "import re;print(re.search(r'__version__\s*=\s*\"([^\"]+)\"', open('$(ROOT_SRC)/$(PKG)/__init__.py').read()).group(1))")

DEB      := $(BUILD)/$(PKG)_$(VERSION)_all.deb

PYTHON ?= python3

.PHONY: all deb control clean check

all: deb

# ---------------------------------------------------------------- 组装
$(DEST)/.stamp: $(shell find $(ROOT_SRC)/$(PKG) -name '*.py')
	@rm -rf "$(DEST)"
	@mkdir -p "$(DEST)/DEBIAN"
	@mkdir -p "$(DEST)/opt/$(PKG)/$(PKG)"
	@mkdir -p "$(DEST)/usr/bin"
	@mkdir -p "$(DEST)/usr/share/doc/$(PKG)"

	# Python 包本体
	@cp -r "$(ROOT_SRC)/$(PKG)/." "$(DEST)/opt/$(PKG)/$(PKG)/"
	@find "$(DEST)/opt/$(PKG)" -type d -name '__pycache__' -prune -exec rm -rf {} + 2>/dev/null || true
	@find "$(DEST)/opt/$(PKG)" -name '*.pyc' -delete 2>/dev/null || true

	# 启动器
	@cp "$(CURDIR)/debian/$(PKG)" "$(DEST)/usr/bin/$(PKG)"
	@chmod 0755 "$(DEST)/usr/bin/$(PKG)"

	# 文档
	@cp "$(CURDIR)/debian/copyright" "$(DEST)/usr/share/doc/$(PKG)/copyright"
	@if [ -f "$(ROOT_SRC)/README.md" ]; then \
		gzip -9 -n -c "$(ROOT_SRC)/README.md" > "$(DEST)/usr/share/doc/$(PKG)/README.md.gz"; \
	fi
	@if [ -f "$(ROOT_SRC)/LICENSE" ]; then \
		gzip -9 -n -c "$(ROOT_SRC)/LICENSE" > "$(DEST)/usr/share/doc/$(PKG)/LICENSE.gz"; \
	fi

	# 维护脚本
	@cp "$(CURDIR)/debian/postinst" "$(DEST)/DEBIAN/postinst"
	@cp "$(CURDIR)/debian/prerm"    "$(DEST)/DEBIAN/prerm"
	@cp "$(CURDIR)/debian/postrm"   "$(DEST)/DEBIAN/postrm"
	@chmod 0755 "$(DEST)/DEBIAN/postinst" "$(DEST)/DEBIAN/prerm" "$(DEST)/DEBIAN/postrm"

	@touch "$(DEST)/.stamp"

control: $(DEST)/.stamp
	@SIZE=$$(du -sk "$(DEST)" | cut -f1); \
	sed -e "s/@VERSION@/$(VERSION)/g" -e "s/@SIZE@/$$SIZE/g" \
		"$(CURDIR)/debian/control.in" > "$(DEST)/DEBIAN/control"
	@chmod 0644 "$(DEST)/DEBIAN/control"
	@echo "  control 已生成（版本 $(VERSION)）"

deb: control
	@mkdir -p "$(BUILD)"
	@rm -f "$(DEST)/.stamp"
	@rm -f "$(DEB)"
	@dpkg-deb --root-owner-group --build "$(DEST)" "$(DEB)" >/dev/null
	@echo ""
	@echo "  打包完成：$(DEB)"
	@ls -lh "$(DEB)" | awk '{print "  体积　　：" $$5}'
	@echo ""

check: deb
	@dpkg-deb --info "$(DEB)" | head -20
	@echo ""
	@echo "  包内文件："
	@dpkg-deb --contents "$(DEB)" | sed 's/^/    /'

clean:
	@rm -rf "$(BUILD)"
	@echo "  已清理 $(BUILD)"
