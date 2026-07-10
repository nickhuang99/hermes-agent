#!/usr/bin/env python3
"""HTML → text extractor for cppreference and C++ standard pages.
Uses Python stdlib html.parser — zero dependencies."""

from html.parser import HTMLParser
from html import unescape
from pathlib import Path

class CppreferenceParser(HTMLParser):
    """Extract content from cppreference.com MediaWiki HTML pages."""
    SKIP_TAGS = {'script','style','meta','link'}
    SKIP_CLASSES = {'t-navbar','t-navbar-head','t-navbar-menu','t-navbar-sep',
                    'editsection','printfooter','catlinks','t-nv','t-nv-begin',
                    't-nv-ln-table','toc'}
    
    def __init__(self):
        super().__init__()
        self.title = ''
        self.paragraphs = []
        self._current = ''
        self._in_title = False
        self._in_content = False
        self._skipping = None  # tag name of element we're skipping
        
    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        aid = attrs.get('id','')
        cls = attrs.get('class','')
        
        if tag == 'title':
            self._in_title = True
        elif aid == 'mw-content-text':
            self._in_content = True
        elif self._skipping or not self._in_content:
            return  # already skipping or not in content yet
            
        # Start skipping this element and all its children
        if tag in self.SKIP_TAGS:
            self._skipping = tag
        elif cls and self.SKIP_CLASSES & set(cls.split()):
            self._skipping = tag
        # Block elements → flush
        elif tag in ('p','h1','h2','h3','h4','h5','h6','li','div','pre','table','tr','br','hr'):
            self._flush()
            
    def handle_endtag(self, tag):
        if tag == 'title':
            self._in_title = False
        elif self._skipping == tag:
            self._skipping = None  # stop skipping when matching end tag
        elif self._skipping:
            return
        elif not self._in_content:
            return
        elif tag in ('p','h1','h2','h3','h4','h5','h6','li','div','table','tr'):
            self._flush()
            
    def handle_data(self, data):
        if self._in_title:
            self.title += data
            return
        if self._skipping or not self._in_content:
            return
        text = data.strip()
        if text:
            if self._current:
                self._current += ' ' + text
            else:
                self._current = text
                
    def _flush(self):
        if self._current:
            t = unescape(self._current).strip()
            # Filter noise: version markers, too-short fragments, punctuation-only
            if (t and len(t) > 5 and not t.startswith('(C++')
                and not t.startswith('(since C++')
                and t not in (';','.',':','-')):
                self.paragraphs.append(t)
            self._current = ''
            
    def get_result(self):
        self._flush()
        title = self.title.split(' - ')[0].strip()
        return title, '\n\n'.join(self.paragraphs)


class CxxStandardParser(HTMLParser):
    """Extract content from C++ ISO standard HTML pages.
    
    - Title: from <title> tag (format: [vector.data])
    - Code: from <code class="itemdeclcode"> → unescape HTML entities
    - Description: from <div class="texpara">, <div class="itemdescr">
    """
    def __init__(self):
        super().__init__()
        self.title = ''
        self.paragraphs = []
        self._in_title = False
        self._in_code = False
        self._in_desc = False
        self._skip = 0
        self._current = ''
        
    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        cls = attrs.get('class','')
        
        if tag == 'title':
            self._in_title = True
            return
            
        if tag in ('script','style','link'):
            self._skip += 1
            return
            
        # Code blocks
        if tag == 'code' and 'itemdeclcode' in cls:
            self._in_code = True
            self._flush()
            return
            
        # Description blocks
        if cls and any(c in cls for c in ('texpara','itemdescr','para','sentence')):
            self._in_desc = True
            
        # Block elements
        if tag in ('p','h1','h2','h3','h4','div','br'):
            self._flush()
            
    def handle_endtag(self, tag):
        if tag == 'title':
            self._in_title = False
            return
        if self._skip > 0:
            self._skip -= 1
            return
        if self._in_code and tag == 'code':
            if self._current:
                self.paragraphs.append('[CODE] ' + unescape(self._current.strip()))
                self._current = ''
            self._in_code = False
        if tag in ('p','h1','h2','h3','h4','div'):
            self._flush()
            
    def handle_data(self, data):
        if self._in_title:
            self.title += data
            return
        if self._skip > 0:
            return
        text = data.strip()
        if text:
            if self._current and self._current[-1] not in ('\n',' ','(','<','>',':'):
                self._current += ' '
            self._current += text
            
    def _flush(self):
        if self._current:
            t = unescape(self._current).strip()
            # Filter navigation crumbs (short lines with only punctuation)
            if t and len(t) > 5 and not t.startswith('['):
                prefix = '[CODE] ' if self._in_code else ''
                self.paragraphs.append(prefix + t)
            self._current = ''
            
    def get_result(self):
        self._flush()
        title = self.title.strip()
        if title.startswith('[') and title.endswith(']'):
            title = title[1:-1]
        return title, '\n\n'.join(self.paragraphs)


def extract_cppreference(html_path):
    p = CppreferenceParser()
    with open(html_path) as f:
        p.feed(f.read())
    return p.get_result()

def extract_cxxstandard(html_path):
    p = CxxStandardParser()
    with open(html_path) as f:
        p.feed(f.read())
    return p.get_result()

if __name__ == '__main__':
    import sys
    if len(sys.argv) < 2:
        print("Usage: html_parser.py <file.html> [cpp|cxx]")
        sys.exit(1)
    
    path = sys.argv[1]
    fmt = sys.argv[2] if len(sys.argv) > 2 else 'cpp'
    
    extract = extract_cppreference if fmt == 'cpp' else extract_cxxstandard
    title, body = extract(path)
    
    print(f"TITLE: {title}")
    print(f"LENGTH: {len(body)} chars")
    print(f"PARAGRAPHS: {len(body.split(chr(10)+chr(10)))}")
    print()
    print(body[:2000])
