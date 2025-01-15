import json
import os
from pathlib import Path
import streamlit as st
import markdown2
import yaml

def load_article_contexts(articles_dir):
    """
    Load article contexts and their keywords from a directory.
    Each article has two files:
    - article_name.md: The article content in markdown
    - article_name.yaml: The keywords and their definitions

    Args:
        articles_dir (str): Path to the directory containing article files

    Returns:
        dict: Dictionary containing for each article:
            - 'content': Raw markdown content for the API context
            - 'html': HTML rendered content for display
            - 'keywords': List of keywords and their definitions
    """
    articles_dir = Path(articles_dir)

    # Load the index file
    try:
        with open(articles_dir / 'articles_index.json', 'r', encoding='utf-8') as f:
            articles_index = json.load(f)
    except Exception as e:
        st.error(f"Error loading articles index: {str(e)}")
        return {}

    # Load each article's content and keywords
    article_contexts = {}
    for title, files in articles_index.items():
        try:
            # Load markdown content
            with open(articles_dir / files['content'], 'r', encoding='utf-8') as f:
                content = f.read()

            # Load keywords
            with open(articles_dir / files['keywords'], 'r', encoding='utf-8') as f:
                keywords = yaml.safe_load(f)

            article_contexts[title] = {
                'content': content,  # Raw content for API
                'html': markdown2.markdown(content),  # Rendered HTML for display
                'keywords': keywords  # Keywords and definitions
            }
        except Exception as e:
            st.error(f"Error loading article {title}: {str(e)}")
            continue

    return article_contexts

def display_article_and_keywords(article_data):
    """
    Display the article content and keywords in Streamlit.

    Args:
        article_data (dict): Dictionary containing article content, HTML, and keywords
    """
    # Add some styling
    st.markdown("""
        <style>
            .article-container {
                padding: 20px;
                background-color: #f8f9fa;
                border-radius: 5px;
                margin: 10px 0;
            }
            .keyword-title {
                color: #1f77b4;
                font-weight: bold;
            }
        </style>
    """, unsafe_allow_html=True)

    # Create two columns
    col1, col2 = st.columns([2, 1])

    # Display article in left column
    with col1:
        with st.expander("Article Content", expanded=False):
            st.markdown(article_data['html'], unsafe_allow_html=True)

    # Display keywords in right column
    with col2:
        with st.expander("Key Terms", expanded=False):
            for keyword in article_data['keywords']:
                st.markdown(f"**{keyword['term']}**")
                st.markdown(keyword['definition'])
                st.markdown("---")

def get_instructions_template(selected_context):
    """
    Return the instructions template with the selected context inserted.

    Args:
        selected_context (str): The context to insert into the template

    Returns:
        str: Complete instructions with context
    """
    return f'''
<purpose>
You are "Cambium Bot"
I want you to help the user with verbal English. Given the article below, ask
the usr a question about its' content. Wait for the user to answer and then give
him feedback about his English (both pronunciation and grammer). Wait for the
users' answer, and focus on his English rather then the actual answer.
</purpose>
<instructions>
1. Start by introducing yourself and the purpose, and give a brief overview of the
article.
2. The bot's first question will always be "What level of training would you like?" and will present the user with the following difficulty levels:
- Easy level: The bot will provide simple answers and comments, focusing on the important words from the word list.
- Medium level: The bot will offer corrections and suggestions for improvement while maintaining a flowing conversation.
- Advanced level: The bot will provide full corrections, including grammatical structure and improved wording.
3. After each response from the user, ask the question "Would you like to try again? Or
should we try a different question" and respond accordingly.
4. Focus on correcting and guiding correct English, highlighting errors and providing suggestions for improvement.
</instructions>
<context>
{selected_context}
</context>
    '''
