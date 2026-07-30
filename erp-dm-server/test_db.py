from database.db_manager import DatabaseManager

db = DatabaseManager("data/game.db")

# Create a character
char_id = db.create_character(
    name="Elara",
    character_type="NPC",
    full_card_text="This is a 4000 token character card..."
)
print(f"Created character with ID: {char_id}")

# Add emotional state
db.update_emotional_state(char_id, {
    "trust": 90,
    "fear": 10,
    "arousal": 40,
    "tension": 30,
    "intimacy": 60,
    "mood": "playful",
    "emotional_shift": "Just met the player"
})

# Add mechanical stats (D&D)
db.update_mechanical_stats(char_id, {
    "strength": 10,
    "dexterity": 16,
    "constitution": 12,
    "intelligence": 14,
    "wisdom": 10,
    "charisma": 18,
    "hp_current": 20,
    "hp_max": 20,
    "armor_class": 15,
    "level": 1
})

# Retrieve and print
char = db.get_character(char_id)
emotions = db.get_emotional_state(char_id)
stats = db.get_mechanical_stats(char_id)

print(f"Character: {char['name']}")
print(f"Emotions: {emotions}")
print(f"Stats: {stats}")