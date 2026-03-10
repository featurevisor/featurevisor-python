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
