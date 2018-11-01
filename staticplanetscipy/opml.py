# -*- coding: utf-8 -*-

# Licensed under a 3-clause BSD style license - see LICENSE.rst
# Copyright (c) 2018 Pauli Virtanen

"""
Minimal OPML feed list writer.
"""
from __future__ import absolute_import, division, unicode_literals, print_function

import sys
import datetime
import hashlib
import xml.etree.ElementTree as etree
import time
import email.utils

__all__ = ['write_opml']


XML_NS = "{http://www.w3.org/XML/1998/namespace}"
OPML_NS = "{http://opml.org/spec2}"


def write_opml(dest, entries, title=None, updated=None):
    """
    Write OPML feed list to a file.

    Parameters
    ----------
    dest : str
        Destination file path, or a file-like object
    entries : list of (feed_url, html_url, title)
        OPML entries.
    title : str, optional
        Title for the feed list
    updated : datetime.datetime, optional
        Update date for the feed list

    """
    root = etree.Element(OPML_NS + 'opml', attrib={OPML_NS + "version": "2.0"})

    # head (obligatory)
    head = etree.SubElement(root, OPML_NS + 'head')

    # title (optional)
    if title is not None:
        el = etree.SubElement(head, OPML_NS + 'title')
        el.text = title

    # dateModified/Created (optional)
    if updated is not None:
        datefmt = email.utils.formatdate(updated.timestamp())

        el = etree.SubElement(head, OPML_NS + 'dateCreated')
        el.text = datefmt

        el = etree.SubElement(head, OPML_NS + 'dateModified')
        el.text = datefmt

    # body (obligatory)
    body = etree.SubElement(root, OPML_NS + 'body')

    # outline
    for feed_url, html_url, title in entries:
        etree.SubElement(body, OPML_NS + 'outline',
                         attrib={OPML_NS + "text": title,
                                 OPML_NS + "type": "rss",
                                 OPML_NS + "htmlUrl": html_url,
                                 OPML_NS + "xmlUrl": feed_url})

    tree = etree.ElementTree(root)

    def write(f):
        tree.write(f, xml_declaration=True, default_namespace=OPML_NS[1:-1],
                   encoding=str('utf-8'))

    if hasattr(dest, 'write'):
        write(dest)
    else:
        with open(dest, 'wb') as f:
            write(f)
