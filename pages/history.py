import streamlit as st
import pymongo
from datetime import datetime
import gridfs
import os

# Connexion à MongoDB
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
DB_NAME = "streamlit_db"

client = pymongo.MongoClient(MONGO_URI)
db = client[DB_NAME]
history_collection = db["history"]
fs = gridfs.GridFS(db)

st.title("📜 Historique des Conversations")

# Récupérer les entrées d'historique
history_data = list(history_collection.find().sort("timestamp", -1))

if not history_data:
    st.info("Aucun historique disponible.")
else:
    for entry in history_data:
        st.subheader(f"📝 Requête : {entry['query']}")
        st.write(f"📅 Date : {entry['timestamp'].strftime('%Y-%m-%d %H:%M:%S')}")
        st.write(f"📂 Format de réponse : {entry.get('format', 'Non spécifié')}")

        # Affichage de la réponse stockée
        with st.expander("🔍 Voir la réponse"):
            st.markdown(entry["response"])

        # Bouton pour continuer la discussion sur ce fichier
        if st.button(f"💬 Continuer la discussion", key=str(entry["_id"])):
            st.session_state["selected_history_id"] = str(entry["_id"])
            st.switch_page("app.py")  # Retourner à la page principale

