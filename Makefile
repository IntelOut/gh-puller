.PHONY: lint typecheck test clean docker-build docker-run

lint:
	ruff check gitgrab.py

typecheck:
	mypy gitgrab.py

pylint:
	pylint gitgrab.py

test:
	pytest -v

check: lint typecheck pylint test

clean:
	rm -rf __pycache__ .pytest_cache .mypy_cache .ruff_cache
	rm -f *.pyc *.pyo

docker-build:
	docker build -t gitgrab .
	docker tag gitgrab ghcr.io/intelout/gitgrab:latest

docker-run:
	docker run --rm -it \
		-e GITHUB_TOKEN="${GITHUB_TOKEN}" \
		-e GITHUB_USERNAME="${GITHUB_USERNAME}" \
		-e PULL_INTERVAL="${PULL_INTERVAL:-3600}" \
		-v "/data/repos:/data/repos" \
		gitgrab
