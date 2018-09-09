PYTHON=python3

all: build

env:
	$(PYTHON) -mvenv env
	./env/bin/pip install pip wheel setuptools
	./env/bin/pip install -r requirements.txt

env-upgrade:
	./env/bin/python -mpip install --upgrade-strategy=only-if-needed -r requirements.txt
	./env/bin/python -mpip install -U --upgrade-strategy=only-if-needed `./env/bin/python -mpip freeze|sed -e 's/==.*//'`

build: env env-upgrade
	./env/bin/python -mstaticplanetscipy config.json
# Clear web cache
	-find cache/web-cache/ -type f -a -mtime +5 -delete
	-find cache/web-cache/ -empty -delete

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

.PHONY: all build gh-pages clean env-upgrade
