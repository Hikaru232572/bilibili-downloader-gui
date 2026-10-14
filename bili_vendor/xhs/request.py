"""Controlled replacement for Image's URL-format helper.

The upstream Image implementation only uses Html.format_url. Importing the
full upstream request module would pull in its application and settings.
"""
import re


class Html:
    @staticmethod
    def format_url(url):
        if not isinstance(url,str):
            return ''
        return re.sub(r'\\u([0-9a-fA-F]{4})',lambda m:chr(int(m[1],16)),url).replace('\\/','/')
