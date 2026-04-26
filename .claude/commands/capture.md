# Capture

Quickly capture a thought, idea, task, or note. Routes straight to Notion.

## Input
$ARGUMENTS

## Instructions

1. **Read the input.** If nothing was provided, ask: "What would you like to capture?"

2. **Classify automatically** based on the text:
   - "I need to..." / "remind me to..." / "add task..." → Task
   - "I had an idea..." / "what if..." / "idea:" → Idea
   - "I just met..." / "met someone..." → Person
   - "Save this..." / "article:" / "book:" / "read:" → Reading List
   - "Remember..." / general thought / journal → Daily Log
   - Event with a date → Calendar

   If unclear, ask: "Is this a Task, Idea, Note, or something to read?"

3. **Route to the correct Notion database:**

   - **Task** → Tasks (c609605c-2bfc-4cf8-9762-39a8c97e4f0a)
     Required fields: Name, Due date (ask if not provided), Priority (default: Medium)

   - **Idea** → Ideas (3bce9219-2306-47a5-b48c-8a00ffa1c148)
     Fields: title + brief content with the idea and any context

   - **Person** → People (a5fc22e0-ea3a-4619-a8b0-9f10ee17ebcc)
     Fields: Name + how you met / context

   - **Reading List** → Reading List (6e262baf-bb06-40ac-936f-6d5e8c8e3961)
     Fields: title + source/URL if available

   - **Note / thought / journal** → Daily Log (c7017459-3ee6-4037-8aaa-f7424cf77ffe)
     Fields: Title (short), Type: Note, Date: today, Content: full text

   - **Event** → Calendar (bc45d69f-7093-400a-a654-f6a9f23ec5d3)
     Fields: Name + date

4. **Confirm.** One line: "Saved to [Database name] in Notion. ✓"
   Include the title or name of what was saved.

## Routing Rules (from CLAUDE.md)
- "I need to..." or "remind me to..." → Tasks
- "I had an idea..." or "what if..." → Ideas
- "I just met..." → People
- "Save this article/book..." → Reading List
- "Remember that..." or general thought → Daily Log
- Goals or weekly focus → Goals
- Events with dates → Calendar

## When to Use
Anytime you want to save something fast without thinking about where it goes.
Type: /capture [your thought]
