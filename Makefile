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
