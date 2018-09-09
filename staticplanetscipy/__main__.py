# -*- coding: utf-8 -*-

# Licensed under a 3-clause BSD style license - see LICENSE.rst
# Copyright (c) 2018 Pauli Virtanen

"""
python -mstaticplanetscipy config.json

Generate static RSS aggregator site.
"""
import os
import sys
import argparse
import json
import hashlib
import shutil
import time
import collections
import datetime

import jinja2
import requests
import feedparser
import bleach

try:
    import requests_cache
except ImportError:
    requests_cache = None

from urllib.parse import quote_plus

from . import atom


Feed = collections.namedtuple(
    'Feed',
    ['title', 'url'])


FeedItem = collections.namedtuple(
    'FeedItem',
    ['feed', 'url', 'date', 'title', 'description'])


def main():
    p = argparse.ArgumentParser(usage=__doc__.lstrip())
    p.add_argument('config', help="Configuration file")
    args = p.parse_args()

    # Load config
    with open(args.config, 'r') as f:
        config = json.load(f)

    base_dir = os.path.dirname(os.path.abspath(args.config))
    cache_dir = os.path.join(base_dir, 'cache')
    template_dir = os.path.join(base_dir, 'template')
    html_dir = os.path.join(base_dir, 'html')

    date_cache_file = os.path.join(cache_dir, 'date_cache.json')
    index_file = os.path.join(template_dir, 'index.html')

    if os.path.exists(date_cache_file):
        with open(date_cache_file, 'r') as f:
            date_cache = json.load(f)
    else:
        date_cache = {}

    if not os.path.isdir(cache_dir):
        os.makedirs(cache_dir)

    if os.path.isdir(html_dir):
        shutil.rmtree(html_dir)
    os.makedirs(html_dir)

    with open(index_file, 'r') as f:
        template = jinja2.Template(f.read(), autoescape="html")

    # Setup requests_cache, if available
    if requests_cache is not None:
        requests_cache.install_cache(os.path.join(cache_dir, 'requests_cache'),
                                     expire_after=config['expire_secs'])

    # Fetch
    print("\nFetching:")

    files = {}
    for url in config['feeds']:
        try:
            files[url] = fetch_url(url, cache_dir, config['expire_secs'])
        except Exception as exc:
            print("FAIL: {0}: {1}".format(url, exc))

    # Parse
    print("\nParsing:")

    feeds = []
    items = []

    failed_urls = []
    
    for url, fn in files.items():
        try:
            data = feedparser.parse(fn)

            feed = Feed(title=data['feed']['title'],
                        url=data['feed']['link'])
            feeds.append(feed)

            entries = data['entries']
        except Exception as exc:
            print("FAIL: {0}: feed: {1}".format(url, exc))
            failed_urls.append(url)
            continue

        num_items = 0

        for entry in entries:
            # Truncate and sanitize HTML content
            try:
                content = entry.get("summary_detail", None)
                if content:
                    content = content.value.strip()
                if not content:
                    content = entry.get("summary", "")
                content = sanitize_html(content,
                                        config["truncate_words"],
                                        entry["link"])
            except Exception as exc:
                print("FAIL: {0}: content: {1}".format(url, exc))
                continue

            try:
                date = entry.get('published_parsed', entry.get('updated_parsed'))
                if date is not None:
                    date = datetime.datetime(*date[0:6])
                else:
                    date = None

                feeditem = FeedItem(
                    feed=feed,
                    url=entry['link'],
                    title=entry.get('title', "Untitled"),
                    date=date,
                    description=content)
                items.append(feeditem)
                num_items += 1
            except Exception as exc:
                print("FAIL: {0}: entry: {1}".format(url, exc))
                continue

        print("OK  : {0}: {1} items".format(url, num_items))

    # Update date cache (we don't fully trust feed date information)
    print("\nProcessing...")
    
    new_date_cache = {}
    for item in items:
        item_id = get_item_id(item)
        if item_id in date_cache:
            new_date_cache[item_id] = date_cache[item_id]
            continue
        new_date_cache[item_id] = time.time()

    date_cache = new_date_cache

    # Backfill dateless feed item dates
    for j, item in enumerate(items):
        if item.date is None:
            item_id = get_item_id(item)
            items[j] = FeedItem(feed=item.feed,
                                url=item.url,
                                title=item.title,
                                date=datetime.datetime.fromtimestamp(date_cache[item_id]),
                                description=item.description)

    # Date sort (allow the feed only set an earlier date than the
    # first time we saw the item)
    def sort_key(item):
        item_id = get_item_id(item)
        return min(datetime.datetime.fromtimestamp(date_cache[item_id]), item.date)

    items.sort(key=sort_key, reverse=True)
    feeds.sort(key=lambda item: item.title.lower())

    # Limit items
    del items[config['max_items']:]

    # Produce HTML
    print("\nWriting HTML...")

    updated = datetime.datetime.utcnow()

    html = template.render(
        title=config["title"],
        url=config["url"],
        feeds=feeds,
        items=items,
        updated=updated,
        failed_urls=failed_urls)

    with open(os.path.join(html_dir, 'index.html'), 'w') as f:
        f.write(html)

    for fn in os.listdir(template_dir):
        if fn != os.path.basename(index_file):
            shutil.copytree(os.path.join(template_dir, fn),
                            os.path.join(html_dir, fn))

    # Produce Atom feed
    print("\nWriting Atom...")
    
    atom_entries = []
    for item in items:
        entry = atom.FeedEntry(title=item.title,
                               link=item.url,
                               author=item.feed.title,
                               author_uri=item.feed.url,
                               updated=item.date,
                               content=item.description,
                               id_context=[item.feed.url, item.url])
        atom_entries.append(entry)

    atom.write_atom(os.path.join(html_dir, "feed.xml"),
                    atom_entries,
                    address=config["address"],
                    author=config["title"],
                    title=config["title"],
                    link=config["url"])


def get_filename(url, cache_dir):
    return os.path.join(cache_dir,
                        hashlib.sha256(url.encode('utf-8')).hexdigest()[:32])


def get_item_id(feed_item):
    h = hashlib.sha256()
    h.update(feed_item.feed.url.encode('utf-8'))
    h.update(feed_item.url.encode('utf-8'))
    h.update(feed_item.title.encode('utf-8'))
    h.update(feed_item.description.encode('utf-8'))
    return h.hexdigest()


def sanitize_html(content, truncate_words, link):
    content = bleach.clean(content, strip=True)
    parts = content.split(" ")
    if len(parts) > truncate_words:
        content = " ".join(parts[:truncate_words])
        content = bleach.clean(content, strip=True)
        content += ' <a href="{0}">(continued...)</a>'.format(link)
    return content


def fetch_url(url, cache_dir, expire_time):
    filename = get_filename(url, cache_dir)

    # Don't fetch if mtime new enough
    try:
        stat = os.stat(filename)
        if time.time() < stat.st_mtime + expire_time:
            print("{0} (cached): {1}".format(url, os.path.basename(filename)))
            return filename
    except OSError:
        pass

    # Fetch
    print("{0}: {1}".format(url, os.path.basename(filename)))
    headers = {'User-agent': 'staticplanetscipy'}
    try:
        with requests.get(url, headers=headers, stream=True) as r, open(filename, 'wb') as f:
            shutil.copyfileobj(r.raw, f)
    except:
        if os.path.exists(filename):
            os.unlink(filename)
        raise

    return filename


if sys.version_info[0] < 3:
    raise RuntimeError("Python 3 required.")

main()
