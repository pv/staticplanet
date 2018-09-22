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
import locale

import jinja2
import requests
import feedparser
import bleach

try:
    from cachecontrol import CacheControl
    from cachecontrol.caches.file_cache import FileCache
except ImportError:
    CacheControl = None

from urllib.parse import quote_plus

from . import atom


Feed = collections.namedtuple(
    'Feed',
    ['id', 'title', 'url'])


FeedItem = collections.namedtuple(
    'FeedItem',
    ['feed', 'url', 'date', 'title', 'description'])


def main():
    p = argparse.ArgumentParser(usage=__doc__.lstrip())
    p.add_argument('config', help="Configuration file")
    args = p.parse_args()

    locale.setlocale(locale.LC_COLLATE, '')

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

    # Fetch
    print("\nFetching:")

    failed_urls = []

    files = {}
    for url in config['feeds']:
        try:
            files[url] = fetch_url(url, cache_dir, config['expire_secs'])
        except Exception as exc:
            print("FAIL: {0}: {1}".format(url, exc))
            failed_urls.append(url)

    # Parse
    print("\nParsing:")

    feeds = []
    items = []

    for url, fn in files.items():
        try:
            data = feedparser.parse(fn)

            feed = Feed(id=url,
                        title=data['feed']['title'],
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

    with open(date_cache_file + '.new', 'w') as f:
        json.dump(date_cache, f)
    os.rename(date_cache_file + '.new', date_cache_file)

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

    def feed_sort_key(item):
        return locale.strxfrm(item.title.lower())

    feeds.sort(key=feed_sort_key)

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
        failed_urls=sorted(failed_urls))

    with open(os.path.join(html_dir, 'index.html'), 'w') as f:
        f.write(html)

    for fn in os.listdir(template_dir):
        if fn == os.path.basename(index_file):
            continue

        src = os.path.join(template_dir, fn)
        dst = os.path.join(html_dir, fn)
        if os.path.isdir(src):
            shutil.copytree(src, dst)
        else:
            shutil.copyfile(src, dst)

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
    tags = ['a', 'abbr', 'acronym', 'b', 'bdi', 'bdo', 'blockquote', 'br',
        'caption', 'cite', 'code', 'col', 'colgroup', 'dd', 'del', 'div',
        'dl', 'dt', 'em', 'hr', 'i', 'img', 'ins', 'kbd', 'li', 'mark',
        'ol', 'p', 'pre', 'q', 'rp', 'rt', 'ruby', 's', 'small', 'span',
        'strong', 'sub', 'sup', 'table', 'tbody', 'td', 'tfoot', 'th',
        'thead', 'tr', 'u', 'ul',
    ]

    content = bleach.clean(content, tags=tags, strip=True)
    parts = content.split(" ")
    if len(parts) > truncate_words:
        content = " ".join(parts[:truncate_words])
        content = bleach.clean(content, tags=tags, strip=True)
        content += ' <a href="{0}">(continued...)</a>'.format(link)
    return content


def fetch_url(url, cache_dir, expire_time):
    filename = get_filename(url, cache_dir)

    # Don't fetch if mtime new enough
    try:
        stat = os.stat(filename)
        if time.time() < stat.st_mtime + expire_time:
            print("CACH: {0}: {1}".format(url, os.path.basename(filename)))
            return filename
    except OSError:
        pass

    # Setup HTTP cache, if available
    session = requests.session()
    if CacheControl is not None:
        web_cache = os.path.join(cache_dir, 'web-cache')
        session = CacheControl(session,
                               cache=FileCache(web_cache))

    # Fetch
    print("GET : {0}: {1}".format(url, os.path.basename(filename)))
    headers = {'User-agent': 'staticplanetscipy'}
    try:
        with session.get(url, headers=headers, stream=True) as r, open(filename + '.new', 'wb') as f:
            for chunk in r.iter_content(chunk_size=65536):
                f.write(chunk)
        os.rename(filename + '.new', filename)
    except:
        try:
            os.unlink(filename)
        except FileNotFoundError:
            pass
        raise
    finally:
        session.close()

    return filename


if sys.version_info[0] < 3:
    raise RuntimeError("Python 3 required.")

main()
