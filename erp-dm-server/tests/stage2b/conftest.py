import sqlite3
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[2]))

from campaign import create_campaign
from database.state_repository import _hash


@pytest.fixture
def rules_free_campaign(tmp_path):
    session = create_campaign(campaign_directory=tmp_path / "campaign")
    conn = sqlite3.connect(session.database_path)
    conn.execute("INSERT INTO locations(id,name,region,description) VALUES(1,'Crossroads','Marches','A fork in the road')")
    conn.execute("INSERT INTO locations(id,name,region,description) VALUES(2,'South Village','Marches','A village south of the crossroads')")
    conn.execute("INSERT INTO characters(id,name,type,current_location_id,status,is_active) VALUES(1,'Arin','PC',1,'active',1)")
    conn.execute("INSERT INTO characters(id,name,type,current_location_id,status,is_active) VALUES(2,'Tanis','NPC',1,'active',1)")
    conn.execute("INSERT INTO characters(id,name,type,current_location_id,status,is_active) VALUES(3,'Mira','NPC',1,'active',1)")
    conn.execute("UPDATE campaigns SET rules_profile_id=NULL,active_scene_id='scene-crossroads',current_turn=0")
    conn.execute("INSERT INTO scene_graph(location_id,object_name,object_state,npc_present,visibility) "
                 "VALUES(1,'south road','open','[2,3]','clear')")
    conn.execute("INSERT INTO conversational_facts(id,character_id,fact_text,importance,confidence,source_type,is_active) "
                 "VALUES('horses',2,'Tanis knows where the horses are kept; goblin tracks were seen south.',0.9,0.8,'narrative',1)")
    conn.execute("INSERT INTO conversational_facts(id,character_id,fact_text,importance,confidence,source_type,is_active) "
                 "VALUES('irrelevant',3,'Mira prefers blue ribbons.',0.1,0.7,'narrative',1)")
    documents = [
        ("aliases-tanis", "narrative.aliases", "narrative.entity", "2", {"aliases": ["Captain Tanis"]}),
        ("dialogue", "narrative.dialogue", "narrative.scene", "scene-crossroads",
         {"last_speaker_id": "2", "conversation_focus_entity_ids": ["2"], "scene_participant_ids": ["1", "2", "3"]}),
        ("memory", "narrative.memory", "narrative.entity", "1", {"summary": "The party paused at the crossroads."}),
        ("world", "narrative.world", "narrative.campaign", session.campaign_id, {"weather": "clear"}),
        ("location-links", "narrative.location", "narrative.location", "1",
         {"connections": [{"direction": "south", "location_id": "2", "name": "South Village"}]})
    ]
    for document_id, namespace, subject_type, subject_id, state in documents:
        canonical = json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        conn.execute("INSERT INTO state_documents(id,campaign_id,namespace,subject_type,subject_id,state_json,revision,content_hash) "
                     "VALUES(?,?,?,?,?,?,1,?)", (document_id, session.campaign_id, namespace, subject_type,
                                                  subject_id, canonical, _hash(canonical)))
    conn.commit()
    conn.close()
    return session
