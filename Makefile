VERSION := $(shell cat plugin/VERSION)
TAG := v$(VERSION)
PY ?= python3

.PHONY: test lint version-check dist clean

test:
	cd tests && $(PY) -m unittest -v

lint:
	sh -n plugin/hooks/spool.sh plugin/bin/chainlog install.sh
	command -v shellcheck >/dev/null && shellcheck -s sh plugin/hooks/spool.sh plugin/bin/chainlog install.sh || true
	$(PY) -m py_compile $$(find plugin/src -name '*.py')

# VERSION, plugin.json, marketplace.json and chainlog --version must agree
version-check:
	@$(PY) -c "import json,sys; v='$(VERSION)'; \
	p=json.load(open('plugin/.claude-plugin/plugin.json'))['version']; \
	m=json.load(open('.claude-plugin/marketplace.json'))['metadata']['version']; \
	sys.path.insert(0,'plugin/src'); import chainlog; c=chainlog.__version__; \
	bad=[n for n,x in (('plugin.json',p),('marketplace.json',m),('__init__',c)) if x!=v]; \
	print('version', v, 'ok' if not bad else 'MISMATCH: '+', '.join(bad)); sys.exit(1 if bad else 0)"

dist: version-check
	rm -rf dist && mkdir -p dist/chainlog-$(TAG)
	tar cf - --exclude '__pycache__' --exclude '*.pyc' .claude-plugin plugin install.sh INSTALL.md README.md \
	  SCHEMA.md SECURITY.md LICENSE llms.txt | (cd dist/chainlog-$(TAG) && tar xf -)
	cd dist && tar czf chainlog-$(TAG).tar.gz chainlog-$(TAG) && rm -rf chainlog-$(TAG)
	cd dist && (shasum -a 256 chainlog-$(TAG).tar.gz 2>/dev/null || sha256sum chainlog-$(TAG).tar.gz) > checksums.txt
	@cat dist/checksums.txt

clean:
	rm -rf dist
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
