#!/usr/bin/python3

import nltk
from nltk.stem import WordNetLemmatizer
import re

"""
Statement pre-processors.
"""


"""
General preprocessor
"""
def preprocess_text(text):
    text = text.lower()
    text = re.sub(r'[^\w\s]', '', text)
    lemmatizer = WordNetLemmatizer()
    tokens = nltk.word_tokenize(text)
    text = ' '.join([lemmatizer.lemmatize(token) for token in tokens])
    return text

def clean_whitespace(statement):
    """
    Remove any consecutive whitespace characters from the statement text.
    """
    import re

    # Replace linebreaks and tabs with spaces
    statement.text = statement.text.replace('\n', ' ').replace('\r', ' ').replace('\t', ' ')

    # Remove any leeding or trailing whitespace
    statement.text = statement.text.strip()

    # Remove consecutive spaces
    statement.text = re.sub(' +', ' ', statement.text)

    return statement


def unescape_html(statement):
    """
    Convert escaped html characters into unescaped html characters.
    For example: "&lt;b&gt;" becomes "<b>".
    """
    import html

    statement.text = html.unescape(statement.text)

    return statement


def convert_to_ascii(statement):
    """
    Converts unicode characters to ASCII character equivalents.
    For example: "på fédéral" becomes "pa federal".
    """
    import unicodedata

    text = unicodedata.normalize('NFKD', statement.text)
    text = text.encode('ascii', 'ignore').decode('utf-8')

    statement.text = str(text)
    return statement
