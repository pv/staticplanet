PYTHON=python3

all: build

env:
	$(PYTHON) -mvenv env
	./env/bin/pip install -r requirements.txt

build: env
	./env/bin/python3 -mstaticplanetscipy config.json

gh-pages: html/index.html
	rm -rf html/.git
	touch html/.nojekyll
	git -C html init
	git -C html add .
	git -C html commit -q -m "Update files"
	git fetch html
	git branch -D gh-pages || true
	git branch gh-pages FETCH_HEAD
	rm -rf html/.git

clean:
	rm -rf cache html env

.PHONY: all build gh-pages clean
