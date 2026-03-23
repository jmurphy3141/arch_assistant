"""Tests for clean_json fence stripping in drawing_agent_server."""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from drawing_agent_server import clean_json


def test_plain_json():
    assert clean_json('{"a":1}') == '{"a":1}'


def test_fenced_json_with_language():
    assert clean_json('```json\n{"a":1}\n```') == '{"a":1}'


def test_fenced_json_without_language():
    assert clean_json('```\n{"a":1}\n```') == '{"a":1}'


def test_fenced_json_no_newlines():
    assert clean_json('```json{"a":1}```') == '{"a":1}'


def test_fenced_json_extra_whitespace():
    result = clean_json('```json\n\n  {"a":1}  \n\n```')
    assert result == '{"a":1}'


def test_empty_string():
    assert clean_json('') == ''


def test_none_like_empty():
    assert clean_json(None) == ''


def test_plain_json_with_whitespace():
    assert clean_json('  {"a":1}  ') == '{"a":1}'
