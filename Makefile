.PHONY: setup-monorepo
setup-monorepo:
	mkdir -p monorepo
	if [ ! -d "monorepo/.git" ]; then \
		git clone git@github.com:featurevisor/featurevisor.git monorepo; \
	else \
		(cd monorepo && git fetch origin main && git checkout main && git pull origin main); \
	fi
	(cd monorepo && make install && make build)

.PHONY: update-monorepo
update-monorepo:
	(cd monorepo && git pull origin main)

.PHONY: setup-golang-sdk
setup-golang-sdk:
	mkdir -p featurevisor-go
	if [ ! -d "featurevisor-go/.git" ]; then \
		git clone git@github.com:featurevisor/featurevisor-go.git featurevisor-go; \
	else \
		(cd featurevisor-go && git fetch origin main && git checkout main && git pull origin main); \
	fi

.PHONY: update-golang-sdk
update-golang-sdk:
	(cd featurevisor-go && git pull origin main)

.PHONY: test test-openfeature typecheck check
test:
	PYTHONPATH=src python3 -m unittest discover -s tests -v

test-openfeature:
	PYTHONPATH=src python3 -m unittest discover -s tests_openfeature -v

typecheck:
	python3 -m mypy

check: test test-openfeature typecheck

.PHONY: test-example-1
test-example-1:
	PYTHONPATH=src python3 -m unittest discover -s tests -v
	PYTHONPATH=src python3 -m featurevisor test --projectDirectoryPath=../featurevisor/examples/example-1 --onlyFailures
