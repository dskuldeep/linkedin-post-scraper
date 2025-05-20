import os
import json
from glob import glob
import networkx as nx
import streamlit as st
from pyvis.network import Network
import tempfile
import pandas as pd

st.set_page_config(layout="wide")
st.title("LinkedIn Knowledge Graph Explorer (Sci-Fi Edition)")

# Sidebar options
max_posts = st.sidebar.slider("Max posts to load", 10, 200, 50)
graph_type = st.sidebar.selectbox("Graph Layout", ["Spring", "Kamada-Kawai", "Circular", "Random"])
show_likes = st.sidebar.checkbox("Show Likes", value=True)
show_comments = st.sidebar.checkbox("Show Comments", value=True)
show_replies = st.sidebar.checkbox("Show Comment Replies", value=True)

# Data loading and graph building
def get_profile_url(author):
    if isinstance(author, dict):
        return author.get('profile_url', None)
    return None

def add_like_edges(G, post_author_url, likers):
    for liker in likers:
        liker_url = liker.get('url')
        if liker_url and post_author_url and liker_url != post_author_url:
            G.add_node(liker_url, label=liker.get('name', liker_url), group='liker')
            G.add_edge(liker_url, post_author_url, label='like', color='#00ff99', physics=True)

def add_comment_edges(G, post_author_url, comments, show_replies=True):
    for comment in comments:
        commenter = comment.get('author', {})
        commenter_url = get_profile_url(commenter)
        content = comment.get('content', '')
        if commenter_url and post_author_url and commenter_url != post_author_url:
            G.add_node(commenter_url, label=commenter.get('name', commenter_url), group='commenter')
            G.add_edge(commenter_url, post_author_url, label='comment', title=content, color='#00bfff', physics=True)
        # Handle replies
        if show_replies:
            for reply in comment.get('replies', []):
                replier = reply.get('author', {})
                replier_url = get_profile_url(replier)
                reply_content = reply.get('content', '')
                if replier_url and commenter_url and replier_url != commenter_url:
                    G.add_node(replier_url, label=replier.get('name', replier_url), group='replier')
                    G.add_edge(replier_url, commenter_url, label='comment', title=reply_content, color='#ff00ff', physics=True)

@st.cache_data(show_spinner=False)
def build_graph(show_likes, show_comments, show_replies, max_posts):
    input_dir = 'posts/clean'
    files = glob(os.path.join(input_dir, '*.json'))[:max_posts]
    G = nx.MultiDiGraph()
    for file in files:
        with open(file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        author = data.get('author', {})
        post_author_url = get_profile_url(author)
        if not post_author_url:
            continue
        G.add_node(post_author_url, label=author.get('name', post_author_url), group='author')
        engagement = data.get('engagement', {})
        if show_likes:
            add_like_edges(G, post_author_url, engagement.get('likers', []))
        if show_comments:
            add_comment_edges(G, post_author_url, engagement.get('comments', []), show_replies=show_replies)
    return G

G = build_graph(show_likes, show_comments, show_replies, max_posts)

# Sci-fi PyVis visualisation
def sci_fi_pyvis(G):
    net = Network(height='800px', width='100%', bgcolor='#0a0a23', font_color='#00ffea', directed=True)
    # Faster physics for preview
    net.barnes_hut(gravity=-2000, central_gravity=0.5, spring_length=100, spring_strength=0.1, damping=0.7)
    net.set_options('''
    var options = {
      "nodes": {
        "borderWidth": 2,
        "borderWidthSelected": 4,
        "color": {
          "border": "#00ffea",
          "background": "#1a1a40",
          "highlight": {"border": "#ff00ff", "background": "#222266"},
          "hover": {"border": "#00ff99", "background": "#222266"}
        },
        "font": {"color": "#00ffea", "size": 16, "face": "Orbitron"},
        "shadow": false
      },
      "edges": {
        "color": {"color": "#00ff99", "highlight": "#ff00ff", "hover": "#00bfff"},
        "smooth": {"type": "dynamic"},
        "width": 1.5,
        "shadow": false
      },
      "physics": {
        "enabled": true,
        "barnesHut": {"gravitationalConstant": -2000, "centralGravity": 0.5, "springLength": 100, "springConstant": 0.1, "damping": 0.7}
      },
      "interaction": {"hover": true, "multiselect": true, "navigationButtons": true, "keyboard": true}
    }
    ''')
    net.from_nx(G)
    with tempfile.NamedTemporaryFile('w', delete=False, suffix='.html') as f:
        net.save_graph(f.name)
        return f.name

# Data science tools
st.sidebar.header("Data Science Tools")
if st.sidebar.button("Show Node Degree Table"):
    degrees = dict(G.degree())
    df = pd.DataFrame(list(degrees.items()), columns=["Profile URL", "Degree"])
    st.dataframe(df.sort_values("Degree", ascending=False))

if st.sidebar.button("Show Top Commenters"):
    commenters = [n for n, d in G.nodes(data=True) if d.get('group') == 'commenter']
    st.write(pd.Series(commenters).value_counts().head(10))

if st.sidebar.button("Show Top Likers"):
    likers = [n for n, d in G.nodes(data=True) if d.get('group') == 'liker']
    st.write(pd.Series(likers).value_counts().head(10))

if st.sidebar.button("Show Connected Components"):
    comps = list(nx.weakly_connected_components(G))
    st.write(f"Number of connected components: {len(comps)}")
    st.write(comps[:5])

# Main sci-fi visualisation
st.subheader("Sci-Fi Interactive Knowledge Graph")
with st.spinner("Rendering knowledge graph..."):
    html_path = sci_fi_pyvis(G)
    with open(html_path, 'r', encoding='utf-8') as f:
        html = f.read()
st.components.v1.html(html, height=850, scrolling=True)

st.markdown("""
**Instructions:**
- Use the sidebar to filter graph features and explore data science tools.
- Hover/click nodes and edges for details.
- The graph is interactive and physics-based for a sci-fi feel.
- Use this tool to identify LinkedIn accounts and their interests based on engagement and comments.
""")
