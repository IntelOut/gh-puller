.PHONY: lint typecheck test clean docker-build docker-run

lint:
	ruff check pull_repos.py

typecheck:
	mypy pull_repos.py

pylint:
	pylint pull_repos.py

test:
	pytest -v

check: lint typecheck pylint test

clean:
	rm -rf __pycache__ .pytest_cache .mypy_cache .ruff_cache
	rm -f *.pyc *.pyo

docker-build:
	docker build -t gh-puller .
	docker tag gh-puller ghcr.io/intelout/gh-puller:latest

docker-run:
	docker run --rm -it \
		-e GITHUB_TOKEN="${GITHUB_TOKEN}" \
		-e GITHUB_USERNAME="${GITHUB_USERNAME}" \
		-e PULL_INTERVAL="${PULL_INTERVAL:-3600}" \
		-v "${PWD}/repos:/home/user/git" \
		gh-puller
