.PHONY: install run clean

UV := $(shell command -v uv 2>/dev/null)
IS_NIXOS := $(shell test -e /etc/NIXOS && echo 1)

install:
ifndef UV
ifeq ($(IS_NIXOS),1)
	@echo "NixOS detected. Install uv with:"
	@echo "  nix-shell -p uv --run 'make install'"
	@echo "Or add uv to your configuration.nix / home-manager."
	@exit 1
else
	@echo "Installing uv..."
	@curl -LsSf https://astral.sh/uv/install.sh | sh
	@echo "Restart your shell or run: source $$HOME/.local/bin/env"
	@echo "Then re-run: make install"
	@exit 1
endif
endif
	uv sync

run:
	uv run ai-trace-scan $(ARGS)

clean:
	rm -rf .venv
