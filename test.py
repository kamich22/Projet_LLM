import streamlit as st
import pymongo
import gridfs
import os
import anthropic
import json
from bson.objectid import ObjectId
from dotenv import load_dotenv
import PyPDF2
from docx import Document
import asyncio
from datetime import datetime
import re

# Charger les variables d'environnement
load_dotenv()

# Connexion MongoDB
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
DB_NAME = "streamlit_db"

client = pymongo.MongoClient(MONGO_URI)
db = client[DB_NAME]
history_collection = db["history"]
fs = gridfs.GridFS(db)

# Initialisation de l'API Claude
client_claude = anthropic.Client(api_key=os.getenv('ANTHROPIC_API_KEY'))

# Configuration de la page
st.set_page_config(page_title="💬 Chat Claude AI", page_icon="🤖", layout="wide")
st.title("🤖 Chat Claude AI - Discussion Interactive")

# **📜 Initialisation de la session**
if "chat_history" not in st.session_state:
    st.session_state["chat_history"] = []
if "file_content" not in st.session_state:
    st.session_state["file_content"] = ""
if "file_id" not in st.session_state:
    st.session_state["file_id"] = None
if "selected_history_id" not in st.session_state:
    st.session_state["selected_history_id"] = None
if "message_batches" not in st.session_state:
    st.session_state["message_batches"] = []

# **🆕 Bouton Nouveau Chat**
if st.sidebar.button("🆕 Nouveau Chat"):
    st.session_state["chat_history"] = []
    st.session_state["file_content"] = ""
    st.session_state["file_id"] = None
    st.session_state["selected_history_id"] = None
    st.session_state["message_batches"] = []
    st.rerun()

# **📂 Upload de fichiers**
st.sidebar.header("📎 Importer un fichier")
uploaded_file = st.sidebar.file_uploader("Choisissez un fichier (PDF, Word)", type=["pdf", "docx"])

def extract_text_from_pdf(pdf_file):
    """Extrait le texte d'un fichier PDF"""
    reader = PyPDF2.PdfReader(pdf_file)
    text = ""
    for page in reader.pages:
        text += page.extract_text() + "\n"
    return text

def extract_text_from_docx(docx_file):
    """Extrait le texte d'un fichier Word"""
    doc = Document(docx_file)
    text = "\n".join([para.text for para in doc.paragraphs])
    return text

if uploaded_file:
    file_id = fs.put(uploaded_file.read(), filename=uploaded_file.name, content_type=uploaded_file.type)
    st.session_state["file_id"] = str(file_id)
    
    # Extraction du contenu du fichier
    if uploaded_file.type == "application/pdf":
        st.session_state["file_content"] = extract_text_from_pdf(uploaded_file)
    elif uploaded_file.type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
        st.session_state["file_content"] = extract_text_from_docx(uploaded_file)
    
    st.sidebar.success(f"📂 Fichier {uploaded_file.name} sauvegardé et analysé.")

# **🔧 Champs de spécification de la demande**
st.sidebar.header("📝 Spécifications de la demande")
descriptif = st.sidebar.text_area("📄 Descriptif de la fonctionnalité", "")
contexte_fonctionnel = st.sidebar.text_area("⚙️ Contexte fonctionnel", "")
contexte_technique = st.sidebar.text_area("💻 Contexte technique", "")
format_reponse = st.sidebar.selectbox("📂 Format de réponse souhaité", ["Texte", "Tableau"])
exemple_cas = st.sidebar.text_area("🧪 Exemple de cas d'utilisation", "")

# Configuration pour les messages en batches
batch_size = st.sidebar.slider("Taille des lots de messages (batch size)", 1, 10, 5)
max_batches = st.sidebar.slider("Nombre maximum de lots à considérer", 1, 5, 3)

# **🔍 Sélection de l'historique**
with st.sidebar:
    st.header("📜 Historique des conversations")
    history_data = list(history_collection.find().sort("timestamp", -1))

    if history_data:
        selected_history_index = st.selectbox(
            "🔍 Sélectionnez une conversation :", 
            range(len(history_data)), 
            format_func=lambda i: f"{history_data[i].get('query', 'Sans titre')} ({history_data[i].get('timestamp', '').strftime('%Y-%m-%d %H:%M:%S')})"
        )
        
        selected_history = history_data[selected_history_index]
        
        if st.button("📋 Charger cette conversation"):
            st.session_state["selected_history_id"] = str(selected_history["_id"])
            
            # Vérifier si l'entrée a un champ 'messages'
            if "messages" in selected_history and selected_history["messages"]:
                st.session_state["chat_history"] = selected_history["messages"]
                
                # Organiser les messages en lots (batches)
                messages = selected_history["messages"]
                st.session_state["message_batches"] = [
                    messages[i:i+batch_size] for i in range(0, len(messages), batch_size)
                ]
            else:
                # Créer un historique avec la question et la réponse initiale
                initial_messages = [
                    {"role": "user", "content": selected_history.get("query", "")},
                    {"role": "assistant", "content": selected_history.get("response", "")}
                ]
                st.session_state["chat_history"] = initial_messages
                st.session_state["message_batches"] = [initial_messages]
            
            st.session_state["file_id"] = selected_history.get("file_id")
            st.success("Conversation chargée avec succès!")
            st.rerun()

    if st.button("🗑 Effacer tout l'historique"):
        history_collection.delete_many({})
        st.session_state["chat_history"] = []
        st.session_state["message_batches"] = []
        st.rerun()

# **💬 Interface de Chat**
for message in st.session_state["chat_history"]:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

# **📥 Entrée utilisateur**
user_query = st.chat_input("Tapez votre message...")

# Fonction pour organiser les messages en lots (batches)
def organize_messages_in_batches(messages, batch_size):
    return [messages[i:i+batch_size] for i in range(0, len(messages), batch_size)]

# **📡 Fonction pour envoyer la requête à l'API Claude**
async def get_claude_response(prompt: str, message_batches: list, specifications: dict, file_content: str, max_batches: int) -> list:
    # Utiliser seulement les derniers lots de messages (jusqu'à max_batches)
    recent_batches = message_batches[-max_batches:] if len(message_batches) > max_batches else message_batches
    
    # Préparer les messages pour l'API Claude
    claude_messages = []
    
    # Pour chaque lot, créer un résumé ou inclure les messages complets
    for i, batch in enumerate(recent_batches):
        if i < len(recent_batches) - 1:
            # Pour les lots plus anciens, on peut créer un résumé
            batch_summary = "\n".join([f"{msg['role'].capitalize()}: {msg['content'][:100]}..." for msg in batch])
            claude_messages.append({
                "role": "user", 
                "content": f"[Résumé du lot de messages {i+1}]\n{batch_summary}"
            })
            # Ajouter une réponse système pour marquer la séparation
            claude_messages.append({
                "role": "assistant", 
                "content": f"J'ai pris note de ces messages du lot {i+1}."
            })
        else:
            # Pour le lot le plus récent, inclure tous les messages détaillés
            for msg in batch:
                claude_messages.append({"role": msg["role"], "content": msg["content"]})
    
    # Ajouter la question actuelle
    claude_messages.append({"role": "user", "content": prompt})
    
    # Créer le contexte système avec les spécifications
    system_context = f"""
    ### Contexte Fonctionnel :
    {specifications["contexte_fonctionnel"]}

    ### Contexte Technique :
    {specifications["contexte_technique"]}

    ### Format de réponse souhaité :
    IMPORTANT: Vous devez OBLIGATOIREMENT répondre dans le format suivant: {specifications["format_reponse"]}
    - Si "Texte": Répondez en texte continu avec des paragraphes.
    - Si "Tableau": Présentez votre réponse sous forme de tableau structuré en utilisant le formatage markdown.

    ### Gestion des réponses longues :
    Votre réponse doit être complète, peu importe sa longueur. Si votre réponse est très longue, divisez-la en plusieurs parties numérotées (Partie 1/N, Partie 2/N, etc.). Chaque partie doit se terminer par '[SUITE]' si la réponse n'est pas terminée.

    ### Exemple d'utilisation :
    {specifications["exemple_cas"]}

    ### Contenu du fichier (si fourni) :
    {file_content[:1000]}
    
    Important: Vous recevez des messages organisés en lots. 
    Les lots les plus anciens sont résumés, tandis que le lot le plus récent est détaillé.
    Répondez à la dernière question en tenant compte de tout le contexte fourni.
    """
    
    try:
        responses = []
        max_chunks = 10  # Augmenté à 10 pour permettre des réponses bien plus longues
        current_chunk = 1
        
        while current_chunk <= max_chunks:
            # Ajuster le prompt pour les parties suivantes
            if current_chunk > 1:
                part_prompt = f"Continuez votre réponse précédente (Partie {current_chunk}/{max_chunks}). Assurez-vous de terminer votre réponse par [SUITE] si elle n'est pas encore complète."
                claude_messages.append({"role": "user", "content": part_prompt})
            
            response = client_claude.messages.create(
                model="claude-3-5-haiku-20241022",
                max_tokens=8000,  # Augmenté à 8000 tokens
                system=system_context,
                messages=claude_messages
            )
            
            response_text = response.content[0].text
            responses.append(response_text)
            
            # Mettre à jour les messages pour inclure la réponse de Claude
            claude_messages.append({"role": "assistant", "content": response_text})
            
            # Vérifier si la réponse est terminée
            if "[SUITE]" not in response_text:
                break
                
            current_chunk += 1
        
        return responses
    except Exception as e:
        return [f"❌ Erreur lors de l'appel à Claude : {e}"]

# **🚀 Traitement de la requête utilisateur**
if user_query:
    specifications = {
        "descriptif": descriptif,
        "contexte_fonctionnel": contexte_fonctionnel,
        "contexte_technique": contexte_technique,
        "format_reponse": format_reponse,
        "exemple_cas": exemple_cas
    }

    # Ajouter la requête à l'historique et l'afficher
    st.session_state["chat_history"].append({"role": "user", "content": user_query})
    with st.chat_message("user"):
        st.markdown(user_query)
    
    # Mettre à jour les lots de messages (batches)
    st.session_state["message_batches"] = organize_messages_in_batches(
        st.session_state["chat_history"], 
        batch_size
    )
    
    # Obtenir une réponse de Claude en utilisant les lots de messages
    responses = asyncio.run(get_claude_response(
        user_query, 
        st.session_state["message_batches"][:-1],  # Tous les lots sauf celui contenant la dernière requête
        specifications, 
        st.session_state["file_content"],
        max_batches
    ))

    # Traiter chaque partie de la réponse
    full_response = ""
    
    for i, response_part in enumerate(responses):
        # Nettoyer le marqueur [SUITE] pour l'affichage
        clean_response = response_part.replace("[SUITE]", "")
        
        # Afficher chaque partie de la réponse
        with st.chat_message("assistant"):
            if len(responses) > 1:
                part_label = f"**Partie {i+1}/{len(responses)}**\n\n"
                st.markdown(part_label + clean_response)
            else:
                st.markdown(clean_response)
        
        # Ajouter au chat history
        st.session_state["chat_history"].append({"role": "assistant", "content": clean_response})
        full_response += clean_response
    
    # Mettre à jour les lots de messages après la réponse
    st.session_state["message_batches"] = organize_messages_in_batches(
        st.session_state["chat_history"], 
        batch_size
    )

    # Déterminer si c'est une continuation d'une conversation existante
    if st.session_state["selected_history_id"]:
        # Mettre à jour l'entrée existante avec les nouveaux messages
        history_collection.update_one(
            {"_id": ObjectId(st.session_state["selected_history_id"])},
            {"$set": {
                "messages": st.session_state["chat_history"],
                "message_batches": st.session_state["message_batches"],
                "last_update": datetime.now()
            }}
        )
    else:
        # Créer une nouvelle entrée d'historique
        history_entry = {
            "query": user_query,
            "response": full_response,  # Stocker la réponse complète
            "timestamp": datetime.now(),
            "descriptif": descriptif,
            "contexte_fonctionnel": contexte_fonctionnel,
            "contexte_technique": contexte_technique,
            "format_reponse": format_reponse,
            "exemple_cas": exemple_cas,
            "file_id": st.session_state["file_id"],
            "messages": st.session_state["chat_history"],
            "message_batches": st.session_state["message_batches"]
        }
        result = history_collection.insert_one(history_entry)
        # Sauvegarder l'ID de l'historique pour les futures mises à jour
        st.session_state["selected_history_id"] = str(result.inserted_id)