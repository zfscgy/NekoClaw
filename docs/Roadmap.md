## UI
- [x] Fix the message duplication bug
- [x] Merging StreamDeltas in `on_delta`
- [x] Enable media sending/receiving
- [x] Subagent support on GUI
- [x] Config setting tab
- [x] Skill setting tab
- [x] Add time field to StreamDelta
- [x] Show tool call details

## Agent
### Loop
- [x] Fetching subagent/user message in inner loop (ReAct loop)
- [ ] Stop immediately
- [ ] Context compression

### Configuration
- [ ] Enable model-specific configuration for sending back reasoning (support deepseek v4/kimi k2.6)

## Tools
### EXEC
- [x] Python environment packing
- [x] Fix powershell encoding issues (terminal/python) 

### Computer-use
- [ ] Basic computer-use tools like [Claude - Computer Use](https://platform.claude.com/docs/en/agents-and-tools/tool-use/computer-use-tool)

### Web
- [x] Chrome portable support
- [x] Handling the case when browser is closed by user
- [x] WebSearch engine selection config

## Installation
- [x] Windows installation by AutoCython + Pyinstaller