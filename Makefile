.PHONY: install test dashboard skill eval serve
install:   ; pip install -e ".[dev]"
dashboard: ; cd dashboard && npm ci && npm run build
skill:     ; python3 tools/build_skill.py
test:      ; pytest
eval:      ; lucid eval run --split heldout
serve:     ; lucid serve
