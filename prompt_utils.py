import json
import os
from pathlib import Path
import streamlit as st
import markdown2
import yaml

def load_article_contexts(articles_dir):
    """
    Load article contexts, keywords, and translations from a directory.
    """
    articles_dir = Path(articles_dir)

    try:
        with open(articles_dir / 'articles_index.json', 'r', encoding='utf-8') as f:
            articles_index = json.load(f)
    except Exception as e:
        st.error(f"Error loading articles index: {str(e)}")
        return {}

    article_contexts = {}
    for title, files in articles_index.items():
        try:
            # Load English content
            with open(articles_dir / files['content'], 'r', encoding='utf-8') as f:
                content = f.read()

            # Load Hebrew content if available
            content_he = None
            if 'content_he' in files:
                try:
                    with open(articles_dir / files['content_he'], 'r', encoding='utf-8') as f:
                        content_he = f.read()
                except FileNotFoundError:
                    st.warning(f"Hebrew translation file not found for {title}")

            # Load keywords with translations
            with open(articles_dir / files['keywords'], 'r', encoding='utf-8') as f:
                keywords = yaml.safe_load(f)

            article_contexts[title] = {
                'content': content,
                'content_he': content_he,
                'html': markdown2.markdown(content),
                'html_he': markdown2.markdown(content_he) if content_he else None,
                'keywords': keywords,
                'podcast_url': files.get('podcast_url', None)
            }
        except Exception as e:
            st.error(f"Error loading article {title}: {str(e)}")
            continue

    return article_contexts

def get_google_drive_embed_url(file_id):
    """Convert a Google Drive file ID to an embed URL"""
    return f"https://drive.google.com/file/d/{file_id}/preview"

def extract_file_id(url):
    """Extract file ID from a Google Drive URL"""
    if 'id=' in url:
        return url.split('id=')[1]
    elif '/d/' in url:
        return url.split('/d/')[1].split('/')[0]
    return url

def display_article_and_keywords(article_data):
    """
    Display the article content, keywords, and podcast player in Streamlit,
    including Hebrew translations.
    """
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
            .podcast-section {
                margin: 20px 0;
                padding: 15px;
                background-color: #f1f3f4;
                border-radius: 5px;
            }
            .audio-iframe {
                border: none;
                width: 100%;
                height: 60px;
                border-radius: 4px;
            }
            .translation-container {
                display: grid;
                grid-template-columns: 1fr 1fr;
                gap: 20px;
                margin: 10px 0;
            }
            .hebrew-text {
                direction: rtl;
                text-align: right;
                unicode-bidi: bidi-override;
            }

            .rtl-tab {
                direction: rtl !important;
                text-align: right !important;
            }

            .rtl-tab p, .rtl-tab h1, .rtl-tab h2, .rtl-tab h3, .rtl-tab h4, .rtl-tab h5, .rtl-tab h6, .rtl-tab li {
                direction: rtl !important;
                text-align: right !important;
            }

            /* Fix for Streamlit tab button alignment */
            button[role="tab"] {
                direction: ltr;
            }
        </style>
    """, unsafe_allow_html=True)

    # Display podcast player if URL is available
    if article_data.get('podcast_url'):
        with st.expander("📻 Listen to Podcast", expanded=True):
            file_id = extract_file_id(article_data['podcast_url'])
            embed_url = get_google_drive_embed_url(file_id)
            st.markdown(f'<iframe src="{embed_url}" class="audio-iframe"></iframe>',
                       unsafe_allow_html=True)

    # Create two columns for article and keywords
    col1, col2 = st.columns([2, 1])

    # Display article in left column with language tabs
    with col1:
        with st.expander("Brief (generated)", expanded=False):
            tab1, tab2 = st.tabs(["🇺🇸 English", "🇮🇱 עברית"])

            with tab1:
                st.markdown(article_data['html'], unsafe_allow_html=True)

            with tab2:
                if article_data.get('html_he'):
                    st.markdown("""
                        <div class='rtl-tab' dir='rtl' lang='he'>
                            <div class='hebrew-text'>
                                {}
                            </div>
                        </div>
                    """.format(article_data['html_he']), unsafe_allow_html=True)
                else:
                    st.info("תרגום לעברית אינו זמין לתקציר זה")

    # Display keywords in right column with translations
    with col2:
        with st.expander("Keywords you should know (generated)", expanded=False):
            for keyword in article_data['keywords']:
                # English term and definition
                st.markdown(f"**{keyword['term']}**")
                st.markdown(keyword['definition'])

                # Hebrew term and definition (if available)
                if 'term_he' in keyword and 'definition_he' in keyword:
                    st.markdown("<div class='hebrew-text'>", unsafe_allow_html=True)
                    st.markdown(f"**{keyword['term_he']}**")
                    st.markdown(keyword['definition_he'])
                    st.markdown("</div>", unsafe_allow_html=True)
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
2. The bot's first question will always be "What level of English training would you like?" and will present the user with the following difficulty levels:
- Easy level: The bot will provide simple answers and comments, focusing on the important words from the word list.
- Medium level: The bot will offer corrections and suggestions for improvement while maintaining a flowing conversation.
- Advanced level: The bot will provide full corrections, including grammatical structure and improved wording.
3. After each response from the user, ask the question "Would you like to try again? Or
should we try a different question" and respond accordingly.
4. Focus on correcting and guiding correct English, highlighting errors and providing
suggestions for improvement. Keep the questions short and to the point.
</instructions>
<context>
{selected_context}
</context>
    '''
