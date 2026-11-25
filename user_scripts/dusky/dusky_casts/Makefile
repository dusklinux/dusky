.PHONY: run test check clippy fmt install uninstall

run:
	cargo run --frozen --bin dusky_casts

test:
	./scripts/build_on_tmpfs.sh --test

check:
	cargo fmt --check
	./scripts/build_on_tmpfs.sh --clippy

clippy:
	./scripts/build_on_tmpfs.sh --clippy

fmt:
	cargo fmt --check

install:
	./scripts/install.sh

uninstall:
	rm -f -- \
		"$(HOME)/.local/bin/dusky_casts" \
		"$(HOME)/.local/share/dusky/dusky_casts/dusky_casts" \
		"$(HOME)/.local/share/dusky/dusky_casts/binary_manifest.json" \
		"$(HOME)/.local/share/applications/dusky_casts.desktop" \
		"$(HOME)/.local/share/icons/hicolor/scalable/apps/dusky_casts.svg" \
		"$(HOME)/.local/share/licenses/dusky_casts/LICENSE.md"
