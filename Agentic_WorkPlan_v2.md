**KẾ HOẠCH PHÁT TRIỂN**

**Agentic AI SaaS --- Giai Đoạn MVP**

*Multi-Agent · Tool Registry v1 · Order & BI · Eval Framework · Claude
Code*

  ------------------- ---------------------------------------
  **Thời gian**       3 tuần · 21 ngày làm việc
  **Team**            Tech Lead + SE + AI Engineer
  **LLM**             OpenAI GPT-4o / GPT-4o-mini
  **Stack**           Python · FastAPI · LangGraph
  **Tool Registry**   v1 --- YAML config (no token storage)
  **Dev Assistant**   Claude Code --- code, review, eval
  ------------------- ---------------------------------------

**1. Kiến Trúc Tổng Quan**

Hệ thống gồm 4 service độc lập, giao tiếp qua A2A và Tool Registry HTTP
API, deploy bằng Docker Compose:

  ------------------- ---------- -------------- -------------------------------------------------------------------------------------------------------------------------------------
  **Service**         **Port**   **Protocol**   **Trách nhiệm**
  **Orchestrator**    8000       REST / WS      Nhận request, phân loại intent (GPT-4o-mini), lập plan (GPT-4o), giao task cho Domain Agent qua A2A, tổng hợp response
  **Tool Registry**   8001       HTTP REST      Catalog tool từ YAML config; GET /tools · POST /tools/{name}/execute. Forward Bearer token từ header --- không lưu trữ.
  **Order Agent**     8002       A2A + HTTP     A2A server nhận task; tự reasoning (ReAct loop GPT-4o-mini); gọi Tool Registry để search product, get/create customer, create order
  **BI Agent**        8003       A2A + HTTP     A2A server nhận task; tự reasoning NL2SQL (GPT-4o); gọi Tool Registry để execute query PostgreSQL, format kết quả
  ------------------- ---------- -------------- -------------------------------------------------------------------------------------------------------------------------------------

**1.1 Communication Flow**

+----------------------------------------------------------------------+
| **User → Orchestrator:** HTTP POST /chat \| WebSocket /chat/stream   |
| (text input --- client xử lý STT trước khi gửi)                      |
|                                                                      |
| **Orchestrator → Domain Agent:** A2A --- POST /a2a/tasks + GET       |
| /a2a/tasks/{id} (poll)                                               |
|                                                                      |
| **Domain Agent → Tool Registry:** HTTP REST --- GET                  |
| /tools?namespace=X + POST /tools/{name}/execute (MCP-compatible      |
| convention, không dùng MCP SDK)                                      |
|                                                                      |
| **Auth:** Bearer token forward qua HTTP header xuyên suốt chuỗi ---  |
| không lưu, không biến đổi                                            |
+----------------------------------------------------------------------+

**1.1.1 Quyết Định Kiến Trúc --- Không Dùng MCP SDK Cho MVP**

  -------------------- ------------------------------------------- --------------------------------------------------------------
                       **MCP SDK chính thức**                      **HTTP REST (lựa chọn MVP)**
  **Tool discovery**   MCP transport (stdio / SSE / HTTP stream)   GET /tools?namespace=X → JSON (đủ rồi)
  **Tool execution**   MCP protocol message format                 POST /tools/{name}/execute → JSON (đủ rồi)
  **Dependency**       mcp SDK, transport layer, protocol spec     httpx --- đã có sẵn trong stack
  **Debug**            Cần hiểu MCP transport internals            curl/Postman là đủ
  **Convention**       Chuẩn --- tương thích ecosystem rộng        MCP-compatible: cùng endpoint convention, cùng schema format
  -------------------- ------------------------------------------- --------------------------------------------------------------

+----------------------------------------------------------------------+
| **Kết luận:** Tool Registry HTTP API đã follow MCP convention (GET   |
| /tools + POST /tools/execute + OpenAI tool schema). Không cần MCP    |
| SDK cho MVP --- 2 agent cùng mạng, plain HTTP đủ. Khi cần tích hợp   |
| external MCP server hoặc cho Claude.ai/Cursor gọi trực tiếp →        |
| upgrade transport layer sau mà không cần đổi interface.              |
|                                                                      |
| **Post-MVP backlog:** Wrap Tool Registry bằng FastMCP để expose      |
| chuẩn MCP --- tương thích với bất kỳ MCP client nào trong ecosystem. |
+----------------------------------------------------------------------+

**1.2 Model Routing**

  ----------------- ------------------------------------------------------------------ ------------- ----------------------------------------------------------------
  **Model**         **Task type**                                                      **Latency**   **Lý do**
  **gpt-4o-mini**   Intent classify, entity extract, product rerank, response format   \< 1s         Đủ chính xác cho structured output; giá rẻ \~20x so với GPT-4o
  **gpt-4o**        Orchestrator plan, NL2SQL phức tạp, Order reasoning phức tạp       1--3s         Cần reasoning đa bước; escalate khi confidence \< 0.72
  **o1-mini**       Cross-domain plan phức tạp (\> 3 agent), ambiguous edge case       3--10s        Hiếm dùng; chỉ escalate khi GPT-4o fail 2 lần
  ----------------- ------------------------------------------------------------------ ------------- ----------------------------------------------------------------

**1.3 Agent Discovery --- Orchestrator Tự Động Nhận Biết Agents**

  ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
  **Vấn đề:** Nếu hardcode danh sách agent vào system prompt, mỗi khi thêm/xoá/đổi agent phải sửa code và restart Orchestrator. Cách đúng: mỗi Domain Agent tự mô tả bản thân qua Agent Card --- Orchestrator fetch và inject động vào prompt lúc runtime.
  ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------

**A2A Agent Card --- mỗi Domain Agent expose endpoint chuẩn**

\# GET /.well-known/agent.json (mỗi Domain Agent đều có)

\# Orchestrator fetch endpoint này để biết agent có skills gì

\@app.get(\"/.well-known/agent.json\")

async def agent\_card():

return {

\"name\": \"Order Agent\",

\"version\": \"1.0.0\",

\"description\": \"Tạo và quản lý đơn hàng từ tiếng Việt tự nhiên. Hỗ
trợ multi-turn, HITL confirm.\",

\"skills\": \[

{

\"id\": \"create\_order\",

\"name\": \"Tạo đơn hàng\",

\"description\": \"Nhận câu tiếng Việt, extract entities, tạo đơn sau
khi user xác nhận\",

\"examples\": \[\"anh Lâm hai trứng lộn\", \"bàn 3 cho tôi 3 bò kho bánh
mì\"\]

}

\],

\"a2a\_endpoint\": \"http://order-agent:8002/a2a/tasks\",

\"health\": \"http://order-agent:8002/health\"

}

**AgentRegistry trong Orchestrator --- tự fetch & hot-reload**

\# orchestrator/core/agent\_registry.py

class AgentRegistry:

\"\"\"Fetch agent cards định kỳ --- Orchestrator không cần biết trước
agent nào tồn tại.\"\"\"

def \_\_init\_\_(self, seed\_urls: list\[str\], refresh\_interval: int =
60):

self.\_seed\_urls = seed\_urls \# từ env: AGENT\_SEED\_URLS

self.\_refresh\_interval = refresh\_interval

self.\_agents: dict\[str, dict\] = {}

self.\_healthy: set\[str\] = set()

async def start(self):

await self.\_refresh() \# fetch lần đầu khi startup

asyncio.create\_task(self.\_background\_refresh()) \# sau đó refresh mỗi
60s

async def \_refresh(self):

async with httpx.AsyncClient(timeout=5.0) as client:

for url in self.\_seed\_urls:

try:

r = await client.get(f\"{url}/.well-known/agent.json\")

card = r.json()

self.\_agents\[card\[\"name\"\]\] = card

self.\_healthy.add(card\[\"name\"\])

except Exception:

self.\_healthy.discard(self.\_name\_from\_url(url)) \# đánh dấu
unhealthy

def build\_prompt\_context(self) -\> str:

\"\"\"Render agent manifest → inject vào system prompt mỗi
request.\"\"\"

lines = \[\"\#\# Available Domain Agents\\n\"\]

for agent in \[v for k,v in self.\_agents.items() if k in
self.\_healthy\]:

lines.append(f\"\#\#\# {agent\[\'name\'\]} (v{agent\[\'version\'\]})\")

lines.append(f\"{agent\[\'description\'\]}\\n\")

for skill in agent.get(\"skills\", \[\]):

examples = \"; \".join(skill.get(\"examples\", \[\])\[:2\])

lines.append(f\"- \`{skill\[\'id\'\]}\`: {skill\[\'description\'\]}\")

if examples: lines.append(f\" Examples: {examples}\")

lines.append(f\"A2A endpoint: {agent\[\'a2a\_endpoint\'\]}\\n\")

return \"\\n\".join(lines)

**Dynamic Prompt Injection trong Plan Node**

\# Plan node inject agent manifest tại runtime --- luôn dùng danh sách
mới nhất

ORCHESTRATOR\_SYSTEM\_TEMPLATE = \"\"\"

Bạn là Orchestrator của hệ thống Agentic AI.

{agent\_manifest}

\#\# Routing Rules

\- Chỉ route đến agents có trong danh sách \"Available Domain Agents\" ở
trên.

\- Nếu agent không có trong danh sách → không route, thông báo ngoài
phạm vi.

\- Danh sách cập nhật tự động --- đừng assume agent tồn tại nếu không
thấy ở đây.

\"\"\"

class PlanNode:

def \_\_init\_\_(self, registry: AgentRegistry): self.registry =
registry

async def run(self, state: OrchestratorState) -\> OrchestratorState:

system = ORCHESTRATOR\_SYSTEM\_TEMPLATE.format(

agent\_manifest=self.registry.build\_prompt\_context() \# fresh mỗi
request

)

response = await llm\_call(messages=\..., system=system,
task\_type=\"plan\")

\...

\# Khi thêm Inventory Agent mới --- chỉ cần:

\#
AGENT\_SEED\_URLS=http://order-agent:8002,http://bi-agent:8003,http://inventory-agent:8004

\# Orchestrator tự discover trong lần refresh tiếp theo (≤ 60s), không
cần restart.

**1.4 Plan Persistence & Display --- Hiển Thị Kế Hoạch Cho Người Dùng**

  ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
  **Vấn đề:** Plan của Orchestrator hiện chỉ tồn tại trong LangGraph state (Redis). Người dùng không thấy được hệ thống đang làm gì. Giải pháp: Plan node sinh ra 2 layer --- routing plan (internal) và display plan (business-friendly) --- lưu vào PostgreSQL, expose qua API để frontend hiển thị real-time.
  ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------

**Nguyên tắc: Tách Routing Plan và Display Plan**

  -------------------- ---------------------------------------------------- ------------------------------------------------------------
                       **Routing Plan (internal)**                          **Display Plan (for user)**
  **Mục đích**         Orchestrator dùng để điều phối A2A                   Hiển thị cho người dùng biết hệ thống đang làm gì
  **Ngôn ngữ**         Kỹ thuật: agent name, skill id, params               Nghiệp vụ tiếng Việt: rõ nghĩa, không lộ internal
  **Ví dụ goal**       \"steps: \[{agent: bi-agent, skill: bi\_query}\]\"   \"Xem báo cáo doanh thu tháng 3 và tạo đơn cho khách Lâm\"
  **Ví dụ sub-goal**   \"skill: bi\_query, params: {query: \...}\"          \"Truy vấn doanh thu tháng hiện tại\"
  **Lưu trữ**          LangGraph state (Redis, tạm thời)                    PostgreSQL plans + plan\_sub\_goals (persistent)
  -------------------- ---------------------------------------------------- ------------------------------------------------------------

**PostgreSQL Schema**

\-- orchestrator/migrations/001\_plans.sql

CREATE TABLE plans (

id UUID PRIMARY KEY DEFAULT gen\_random\_uuid(),

session\_id VARCHAR(128) NOT NULL,

tenant\_id VARCHAR(128) NOT NULL,

user\_message TEXT NOT NULL,

goal TEXT NOT NULL, \-- VD: \"Xem doanh thu tháng 3 và tạo đơn cho khách
Lâm\"

status VARCHAR(20) NOT NULL DEFAULT \'pending\', \--
pending\|running\|completed\|failed

created\_at TIMESTAMPTZ NOT NULL DEFAULT now(),

updated\_at TIMESTAMPTZ NOT NULL DEFAULT now()

);

CREATE TABLE plan\_sub\_goals (

id UUID PRIMARY KEY DEFAULT gen\_random\_uuid(),

plan\_id UUID REFERENCES plans(id) ON DELETE CASCADE,

sequence INT NOT NULL, \-- thứ tự thực hiện

title TEXT NOT NULL, \-- VD: \"Truy vấn doanh thu tháng hiện tại\"

agent\_name VARCHAR(100) NOT NULL, \-- VD: \"bi-agent\" (tên nội bộ,
dùng cho routing/debug)

agent\_label VARCHAR(100) NOT NULL, \-- VD: \"Báo Cáo & Phân Tích\" (tên
hiển thị cho user)

a2a\_task\_id VARCHAR(128), \-- ID của A2A task khi dispatch; NULL khi
chưa dispatch

status VARCHAR(20) NOT NULL DEFAULT \'pending\',

result\_summary TEXT, \-- VD: \"Doanh thu tháng 3: 125.4 triệu đồng\"

started\_at TIMESTAMPTZ,

completed\_at TIMESTAMPTZ

);

CREATE INDEX idx\_plans\_session ON plans(session\_id, tenant\_id);

CREATE INDEX idx\_sub\_goals\_plan ON plan\_sub\_goals(plan\_id,
sequence);

CREATE INDEX idx\_sub\_goals\_task ON plan\_sub\_goals(a2a\_task\_id);
\-- lookup ngược task → sub\_goal

**Plan Node --- Sinh Dual-Layer Output trong 1 LLM Call**

\# Plan node yêu cầu GPT-4o sinh cả routing + display trong 1 response

PLAN\_NODE\_SYSTEM = \"\"\"

Phân tích yêu cầu và tạo kế hoạch theo 2 layer:

\#\# Layer 1 --- display (cho người dùng thấy)

\- goal: 1 câu tiếng Việt mô tả mục tiêu chính, rõ nghĩa với người không
biết kỹ thuật

\- sub\_goals\[\].title: mô tả từng bước bằng ngôn ngữ nghiệp vụ (KHÔNG
dùng tên agent/tool)

\- sub\_goals\[\].agent\_name: tên nội bộ của agent (order-agent \|
bi-agent) --- dùng cho routing

\- sub\_goals\[\].agent\_label: nhãn hiển thị thân thiện cho người dùng
(xem bảng mapping bên dưới)

\#\# Layer 2 --- routing (cho hệ thống dùng)

\- steps\[\].agent: tên agent nội bộ (order-agent \| bi-agent)

\- steps\[\].skill: skill id lấy từ agent card

\- steps\[\].parameters: params cần truyền cho agent

Agent label mapping (LUÔN dùng nhãn này trong display):

order-agent → \"Tạo & Quản Lý Đơn Hàng\"

bi-agent → \"Báo Cáo & Phân Tích\"

Ví dụ input: \"cho tôi xem doanh thu hôm nay và tạo đơn cho anh Lâm cafe
đen\"

Ví dụ output:

{

\"display\": {

\"goal\": \"Xem doanh thu hôm nay và tạo đơn hàng cho khách hàng Lâm\",

\"sub\_goals\": \[

{\"sequence\":1, \"title\":\"Truy vấn doanh thu trong ngày hôm nay\",
\"agent\_name\":\"bi-agent\", \"agent\_label\":\"Báo Cáo & Phân Tích\"},

{\"sequence\":2, \"title\":\"Tạo đơn hàng cafe đen cho khách Lâm\",
\"agent\_name\":\"order-agent\", \"agent\_label\":\"Tạo & Quản Lý Đơn
Hàng\"}

\]

},

\"routing\": {

\"steps\": \[

{\"agent\":\"bi-agent\", \"skill\":\"bi\_query\",
\"parameters\":{\"query\":\"doanh thu hôm nay\"}},

{\"agent\":\"order-agent\",
\"skill\":\"create\_order\",\"parameters\":{\"message\":\"anh Lâm cafe
đen\"}}

\]

}

}

\"\"\"

**Plan Service --- Lưu & Cập Nhật Trạng Thái**

\# orchestrator/core/plan\_service.py

class PlanService:

def \_\_init\_\_(self, db: asyncpg.Pool): self.db = db

async def create\_plan(self, session\_id, tenant\_id, user\_message,
display: dict) -\> str:

\"\"\"Lưu plan ngay sau khi Plan node chạy xong --- trước khi dispatch
agent.\"\"\"

plan\_id = await self.db.fetchval(

\"INSERT INTO plans(session\_id,tenant\_id,user\_message,goal,status)
VALUES(\$1,\$2,\$3,\$4,\'running\') RETURNING id\",

session\_id, tenant\_id, user\_message, display\[\"goal\"\]

)

for sg in display\[\"sub\_goals\"\]:

await self.db.execute(

\"INSERT INTO
plan\_sub\_goals(plan\_id,sequence,title,agent\_name,agent\_label)
VALUES(\$1,\$2,\$3,\$4,\$5)\",

plan\_id, sg\[\"sequence\"\], sg\[\"title\"\], sg\[\"agent\_name\"\],
sg\[\"agent\_label\"\]

)

return str(plan\_id)

async def link\_task(self, plan\_id: str, sequence: int, a2a\_task\_id:
str):

\"\"\"Gọi ngay sau khi POST /a2a/tasks thành công --- lưu task\_id vào
sub\_goal.\"\"\"

await self.db.execute(

\"UPDATE plan\_sub\_goals SET a2a\_task\_id=\$3, status=\'running\',
started\_at=now()\"

\" WHERE plan\_id=\$1 AND sequence=\$2\",

plan\_id, sequence, a2a\_task\_id

)

async def sync\_from\_task(self, a2a\_task\_id: str, task\_status: str,
result\_summary=None):

\"\"\"Gọi trong polling loop --- tự động sync trạng thái A2A task vào
sub\_goal.

Không cần biết plan\_id hay sequence --- lookup qua
idx\_sub\_goals\_task.\"\"\"

status =
{\"working\":\"running\",\"completed\":\"completed\",\"failed\":\"failed\"}.get(task\_status)

if not status: return

await self.db.execute(

\"\"\"UPDATE plan\_sub\_goals SET status=\$2, result\_summary=\$3,

completed\_at = CASE WHEN \$2!=\'running\' THEN now() ELSE completed\_at
END

WHERE a2a\_task\_id=\$1\"\"\",

a2a\_task\_id, status, result\_summary

)

\# Nếu tất cả sub\_goals của plan đã done → cập nhật plans.status

await self.\_maybe\_complete\_plan(a2a\_task\_id)

async def get\_plan(self, plan\_id: str) -\> dict:

plan = await self.db.fetchrow(\"SELECT \* FROM plans WHERE id=\$1\",
plan\_id)

subs = await self.db.fetch(

\"SELECT \* FROM plan\_sub\_goals WHERE plan\_id=\$1 ORDER BY
sequence\", plan\_id

)

return {\"plan\": dict(plan), \"sub\_goals\": \[dict(s) for s in subs\]}

**API Endpoints --- Frontend Polling**

\# GET /plans/{session\_id}/current --- lấy plan đang chạy của session

\# GET /plans/{plan\_id} --- lấy chi tiết plan theo id

\@router.get(\"/plans/{session\_id}/current\")

async def get\_current\_plan(session\_id: str, plan\_service:
PlanService = Depends()):

row = await plan\_service.db.fetchrow(

\"SELECT id FROM plans WHERE session\_id=\$1 ORDER BY created\_at DESC
LIMIT 1\",

session\_id

)

if not row: raise HTTPException(404, \"No plan found\")

return await plan\_service.get\_plan(str(row\[\"id\"\]))

\# Response example:

\# {

\# \"plan\": {

\# \"id\": \"uuid\", \"goal\": \"Xem doanh thu hôm nay và tạo đơn cho
khách Lâm\",

\# \"status\": \"running\", \"created\_at\": \"2026-03-17T09:00:00Z\"

\# },

\# \"sub\_goals\": \[

\# {\"sequence\":1, \"title\":\"Truy vấn doanh thu hôm nay\",

\# \"agent\_name\":\"bi-agent\", \"agent\_label\":\"Báo Cáo & Phân
Tích\",

\# \"status\":\"completed\", \"result\_summary\":\"Doanh thu hôm nay:
8.4 triệu đồng\"},

\# {\"sequence\":2, \"title\":\"Tạo đơn hàng cafe đen cho khách Lâm\",

\# \"agent\_name\":\"order-agent\", \"agent\_label\":\"Tạo & Quản Lý Đơn
Hàng\", \"status\":\"running\",

\# \"result\_summary\": null}

\# \]

\# }

\# ── Dispatch flow trong Orchestrator (sau khi Plan node chạy) ──

for step in routing\[\"steps\"\]:

\# 1. Dispatch A2A task → nhận task\_id

task\_id = await a2a\_client.submit(step\[\"agent\"\],
step\[\"skill\"\], step\[\"parameters\"\])

\# 2. Lưu task\_id vào sub\_goal ngay lập tức

await plan\_service.link\_task(plan\_id, step\[\"sequence\"\], task\_id)

\# 3. Poll cho đến khi task hoàn thành, đồng thời sync vào DB

while True:

task = await a2a\_client.get\_task(task\_id)

await plan\_service.sync\_from\_task(task\_id, task\[\"status\"\],
task.get(\"result\"))

if task\[\"status\"\] in (\"completed\", \"failed\"): break

await asyncio.sleep(1)

\# Kết quả: plan\_sub\_goals luôn phản ánh đúng trạng thái thực tế của
A2A tasks.

\# Không có manual status management --- mọi thứ tự động qua link
task\_id.

\# Frontend poll GET /plans/{session\_id}/current mỗi 1--2s

\# → render progress UI: goal header + sub-goal checklist với status
badge

**1.5 Domain Agent Autonomous Reasoning --- 3 Lớp Memory**

  --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
  **Mục tiêu:** Domain Agent không chỉ xử lý task hiện tại --- nó học và tích lũy qua thời gian. Ba lớp memory cho phép agent suy luận dựa trên context hiện tại, kiến thức domain tích lũy, và kinh nghiệm từ các task trong quá khứ.
  --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------

**Ba Lớp Memory**

  --------- --------------------- --------------------------------- -----------------------------------------------------------------------------------------------------------------------------------------------------
  **Lớp**   **Tên**               **Lưu ở đâu**                     **Nội dung**
  **1**     **Working Memory**    LangGraph state (Redis)           Context task hiện tại: input, conversation turns, tool results, intermediate reasoning. Tự xoá khi task xong.
  **2**     **Semantic Memory**   PostgreSQL agent\_memory          Facts domain tích lũy lâu dài: alias sản phẩm, preference khách hàng, business rules, SQL pattern đúng. Persist vĩnh viễn, cập nhật theo thời gian.
  **3**     **Episodic Memory**   PostgreSQL agent\_task\_history   Lịch sử các task đã thực hiện: input, outcome, decisions, learnings. Agent dùng để không lặp lại lỗi và nhận ra pattern quen thuộc.
  --------- --------------------- --------------------------------- -----------------------------------------------------------------------------------------------------------------------------------------------------

**PostgreSQL Schema --- Memory Tables**

\-- orchestrator/migrations/002\_agent\_memory.sql

\-- Lớp 2: Semantic Memory --- facts domain tích lũy

CREATE TABLE agent\_memory (

id UUID PRIMARY KEY DEFAULT gen\_random\_uuid(),

agent\_name VARCHAR(100) NOT NULL, \-- \"order-agent\" \| \"bi-agent\"

tenant\_id VARCHAR(128) NOT NULL,

memory\_type VARCHAR(50) NOT NULL,

\-- order-agent:
\"product\_alias\"\|\"customer\_pref\"\|\"order\_pattern\"\|\"vn\_expression\"

\-- bi-agent: \"sql\_pattern\" \|\"glossary\_fix\" \|\"column\_alias\"
\|\"query\_template\"

key TEXT NOT NULL, \-- lookup key (customer\_id, product\_name, \...)

content TEXT NOT NULL, \-- nội dung fact

confidence FLOAT NOT NULL DEFAULT 0.8,

usage\_count INT NOT NULL DEFAULT 0,

last\_used\_at TIMESTAMPTZ,

created\_at TIMESTAMPTZ NOT NULL DEFAULT now(),

updated\_at TIMESTAMPTZ NOT NULL DEFAULT now()

);

CREATE INDEX idx\_mem\_agent\_tenant ON agent\_memory(agent\_name,
tenant\_id);

CREATE INDEX idx\_mem\_type\_key ON agent\_memory(memory\_type, key);

\-- Lớp 3: Episodic Memory --- lịch sử task

CREATE TABLE agent\_task\_history (

id UUID PRIMARY KEY DEFAULT gen\_random\_uuid(),

agent\_name VARCHAR(100) NOT NULL,

tenant\_id VARCHAR(128) NOT NULL,

plan\_id UUID REFERENCES plans(id), \-- link về plan của Orchestrator

skill VARCHAR(100) NOT NULL, \-- \"create\_order\" \| \"bi\_query\"

input\_summary TEXT NOT NULL, \-- tóm tắt input (không lưu raw PII)

outcome VARCHAR(20) NOT NULL, \-- \"success\"\|\"failed\"\|\"cancelled\"

key\_decisions JSONB, \-- decisions agent đã đưa ra

learnings TEXT, \-- facts agent extract được sau task

duration\_ms INT,

created\_at TIMESTAMPTZ NOT NULL DEFAULT now()

);

CREATE INDEX idx\_history\_agent ON agent\_task\_history(agent\_name,
tenant\_id);

CREATE INDEX idx\_history\_skill ON agent\_task\_history(skill,
outcome);

CREATE INDEX idx\_history\_plan ON agent\_task\_history(plan\_id);

**MemoryService --- Retrieve & Store**

\# shared/memory\_service.py (mỗi Domain Agent dùng chung)

class MemoryService:

def \_\_init\_\_(self, db: asyncpg.Pool, agent\_name: str): \...

async def retrieve(self, tenant\_id: str, query\_context: dict, limit=5)
-\> list\[dict\]:

\"\"\"Lấy memories liên quan nhất cho task hiện tại.

Ưu tiên: confidence cao + usage\_count cao + recently used.\"\"\"

rows = await self.db.fetch(\"\"\"

SELECT memory\_type, key, content, confidence, usage\_count

FROM agent\_memory

WHERE agent\_name=\$1 AND tenant\_id=\$2

AND (key ILIKE \$3 OR content ILIKE \$3)

ORDER BY confidence DESC, usage\_count DESC, last\_used\_at DESC

LIMIT \$4

\"\"\", self.agent\_name, tenant\_id,
f\"%{query\_context.get(\'keyword\',\'\')}%\", limit)

\# Cập nhật usage stats

ids = \[r\[\"id\"\] for r in rows if \"id\" in r\]

if ids: await self.\_bump\_usage(ids)

return \[dict(r) for r in rows\]

async def store(self, tenant\_id: str, memory\_type: str, key: str,

content: str, confidence: float = 0.8):

\"\"\"Upsert fact vào memory --- nếu key đã tồn tại thì update content +
confidence.\"\"\"

await self.db.execute(\"\"\"

INSERT INTO
agent\_memory(agent\_name,tenant\_id,memory\_type,key,content,confidence)

VALUES(\$1,\$2,\$3,\$4,\$5,\$6)

ON CONFLICT (agent\_name,tenant\_id,memory\_type,key)

DO UPDATE SET content=\$5,
confidence=GREATEST(agent\_memory.confidence,\$6),

updated\_at=now()

\"\"\", self.agent\_name, tenant\_id, memory\_type, key, content,
confidence)

async def find\_similar\_tasks(self, tenant\_id: str, skill: str,

input\_summary: str, limit=3) -\> list\[dict\]:

\"\"\"Tìm các task tương tự đã thực hiện thành công trong quá khứ.\"\"\"

rows = await self.db.fetch(\"\"\"

SELECT input\_summary, key\_decisions, learnings, duration\_ms

FROM agent\_task\_history

WHERE agent\_name=\$1 AND tenant\_id=\$2 AND skill=\$3 AND
outcome=\'success\'

AND input\_summary ILIKE \$4

ORDER BY created\_at DESC LIMIT \$5

\"\"\", self.agent\_name, tenant\_id, skill,
f\"%{input\_summary\[:30\]}%\", limit)

return \[dict(r) for r in rows\]

async def record\_task(self, tenant\_id: str, plan\_id: str, skill: str,

input\_summary: str, outcome: str,

key\_decisions: dict, learnings: str, duration\_ms: int):

await self.db.execute(\"\"\"

INSERT INTO agent\_task\_history

(agent\_name,tenant\_id,plan\_id,skill,input\_summary,outcome,key\_decisions,learnings,duration\_ms)

VALUES(\$1,\$2,\$3,\$4,\$5,\$6,\$7,\$8,\$9)

\"\"\", self.agent\_name, tenant\_id, plan\_id, skill,

input\_summary, outcome, json.dumps(key\_decisions), learnings,
duration\_ms)

**ReAct Loop Tích Hợp Memory --- Flow Hoàn Chỉnh**

\# domain\_agent/core/react\_loop.py

class MemoryAwareReActLoop:

\"\"\"ReAct loop chuẩn, augment thêm memory retrieval trước khi suy
luận.\"\"\"

async def run(self, task: A2ATask, memory: MemoryService) -\> dict:

t0 = time.monotonic()

\# ── Bước 1: Thu thập context từ memory ──────────────────

semantic\_mem = await memory.retrieve(

tenant\_id = task.tenant\_id,

query\_context = {\"keyword\": task.input\_summary\[:50\]},

limit = 5

)

similar\_tasks = await memory.find\_similar\_tasks(

tenant\_id = task.tenant\_id,

skill = task.skill,

input\_summary = task.input\_summary,

limit = 3

)

\# ── Bước 2: Build system prompt có memory context ────────

system = self.\_build\_system\_prompt(

base\_prompt = AGENT\_SYSTEM\_PROMPT,

semantic\_mem = semantic\_mem, \# facts tích lũy

similar\_tasks = similar\_tasks, \# bài học từ quá khứ

)

\# ── Bước 3: ReAct loop (function calling) ────────────────

messages = \[{\"role\":\"user\",\"content\":
task.parameters\[\"message\"\]}\]

key\_decisions = {}

for iteration in range(10):

resp = await llm\_call(messages, system=system, tools=self.tools)

if resp.tool\_calls:

for tc in resp.tool\_calls:

result = await self.tool\_client.execute(tc.name, tc.arguments)

key\_decisions\[tc.name\] = tc.arguments \# ghi lại decision

messages.append({\"role\":\"tool\",\"content\": json.dumps(result)})

elif \"\[WAITING\_USER\]\" in resp.content:

return {\"status\":\"needs\_input\",\"question\": resp.content}

else:

break \# task hoàn thành

\# ── Bước 4: Extract learnings → lưu vào memory ──────────

learnings = await self.\_extract\_learnings(task, messages,
key\_decisions)

await self.\_store\_learnings(memory, task, learnings)

\# ── Bước 5: Ghi lịch sử task ────────────────────────────

await memory.record\_task(

tenant\_id = task.tenant\_id,

plan\_id = task.plan\_id,

skill = task.skill,

input\_summary = task.input\_summary\[:200\],

outcome = \"success\",

key\_decisions = key\_decisions,

learnings = learnings,

duration\_ms = int((time.monotonic()-t0)\*1000)

)

return {\"status\":\"completed\",\"result\":
messages\[-1\]\[\"content\"\]}

**Learning Extraction --- GPT-4o-mini Sau Mỗi Task**

\# Sau task xong, 1 LLM call nhỏ extract facts hữu ích để lưu vào
semantic memory

EXTRACT\_LEARNINGS\_PROMPT = \"\"\"

Dựa trên task vừa hoàn thành, extract tối đa 3 facts hữu ích cho tương
lai.

Output JSON array, mỗi fact gồm: {memory\_type, key, content,
confidence}.

Các loại fact hữu ích:

order-agent:

\- product\_alias: \"ba đen\" → \"cafe đen đá size L\" (key:
alias\_text)

\- customer\_pref: khách Lâm hay order \"trứng lộn x2\" vào buổi sáng
(key: customer\_id)

\- vn\_expression: \"thêm vào\" = intent add\_to\_existing\_order (key:
expression)

bi-agent:

\- sql\_pattern: query \"doanh thu theo ngày\" → template SQL đúng (key:
query\_intent)

\- glossary\_fix: \"doanh thu\" trong context này = cột net\_revenue
(key: business\_term)

Nếu không có fact nào đáng lưu → trả về \[\].

\"\"\"

async def \_extract\_learnings(self, task, messages, key\_decisions) -\>
str:

conversation = json.dumps(messages\[-4:\]) \# 4 turns cuối

resp = await llm\_call(

messages=\[{\"role\":\"user\",\"content\":f\"Task:
{task.input\_summary}\\nConversation: {conversation}\"}\],

system=EXTRACT\_LEARNINGS\_PROMPT,

task\_type=\"extract\_learnings\" \# dùng gpt-4o-mini

)

return resp.content

async def \_store\_learnings(self, memory, task, learnings\_json: str):

try:

facts = json.loads(learnings\_json)

for fact in facts:

await memory.store(

tenant\_id = task.tenant\_id,

memory\_type = fact\[\"memory\_type\"\],

key = fact\[\"key\"\],

content = fact\[\"content\"\],

confidence = fact.get(\"confidence\", 0.8)

)

except Exception: pass \# learning extraction là best-effort, không
block task

**Memory Injection vào System Prompt**

\# Render memories thành text block inject vào đầu system prompt

def \_build\_system\_prompt(self, base\_prompt, semantic\_mem,
similar\_tasks) -\> str:

blocks = \[base\_prompt\]

if semantic\_mem:

blocks.append(\"\\n\#\# Kiến Thức Tích Lũy (Semantic Memory)\")

for m in semantic\_mem:

blocks.append(f\"- \[{m\[\'memory\_type\'\]}\] {m\[\'key\'\]}:
{m\[\'content\'\]}\")

if similar\_tasks:

blocks.append(\"\\n\#\# Bài Học Từ Task Tương Tự (Episodic Memory)\")

for t in similar\_tasks:

blocks.append(f\"- Task tương tự:
\\\"{t\[\'input\_summary\'\]\[:80\]}\\\"\")

if t\[\"key\_decisions\"\]:

blocks.append(f\" Đã làm: {json.dumps(t\[\'key\_decisions\'\],
ensure\_ascii=False)\[:120\]}\")

if t\[\"learnings\"\]:

blocks.append(f\" Bài học: {t\[\'learnings\'\]\[:100\]}\")

blocks.append(\"\\n\#\# Lưu Ý\")

blocks.append(\"Dùng kiến thức trên để suy luận tốt hơn, nhưng LUÔN ưu
tiên thông tin user cung cấp.\")

return \"\\n\".join(blocks)

**Ví Dụ Memory Theo Từng Agent**

  ----------- ------------------ ------------------------- ------------------------------------------------------------------------
  **Agent**   **memory\_type**   **key**                   **content (ví dụ)**
  **Order**   product\_alias     \"ba đen\"                \"cafe đen đá size L (confidence: 0.95, xuất hiện 12 lần)\"
  **Order**   customer\_pref     \"cust\_123\" (Lâm)       \"Thường order trứng lộn x2 vào buổi sáng, không đường\"
  **Order**   vn\_expression     \"thêm vào\"              \"Intent: add\_to\_existing\_order, KHÔNG phải create\_new\"
  **Order**   order\_pattern     \"bàn 3 shift sáng\"      \"Thường gọi set sáng: cháo trắng + trứng ốp la\"
  **BI**      sql\_pattern       \"doanh thu theo ngày\"   \"SELECT DATE(created\_at), SUM(net\_revenue) FROM orders GROUP BY 1\"
  **BI**      glossary\_fix      \"doanh thu\"             \"= cột net\_revenue (không phải gross\_amount) trong bảng orders\"
  **BI**      column\_alias      \"khách mới\"             \"WHERE created\_at \>= CURRENT\_DATE - INTERVAL \'30 days\'\"
  ----------- ------------------ ------------------------- ------------------------------------------------------------------------

**1.6 Orchestrator Memory --- Học Ở Tầng Meta**

  ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
  **Sự khác biệt quan trọng:** Domain Agent nhớ facts domain (alias sản phẩm, SQL pattern). Orchestrator nhớ ở tầng meta --- routing strategy, plan template, user pattern. Episodic memory của Orchestrator đã có sẵn trong bảng plans/plan\_sub\_goals; chỉ cần thêm semantic memory và tích hợp retrieval vào Plan node.
  ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------

**So Sánh: Orchestrator Memory vs Domain Agent Memory**

  ------------------ ------------------------------------------------------- ----------------------------------------------------
                     **Orchestrator Memory**                                 **Domain Agent Memory**
  **Cấp độ**         Meta --- routing strategy, plan template                Domain --- facts nghiệp vụ cụ thể
  **Học gì**         \"Request loại A → combo agent X+Y hiệu quả hơn X→Z\"   \"ba đen\" = cafe đen đá; doanh thu = net\_revenue
  **Episodic**       Bảng plans + plan\_sub\_goals (đã có, tái sử dụng)      Bảng agent\_task\_history
  **Semantic**       Bảng orchestrator\_memory (mới --- migration 003)       Bảng agent\_memory (migration 002)
  **Dùng khi nào**   Plan node --- trước khi quyết định routing              ReAct loop --- trước khi gọi tool
  ------------------ ------------------------------------------------------- ----------------------------------------------------

**Schema --- orchestrator\_memory (Migration 003)**

\-- orchestrator/migrations/003\_orchestrator\_memory.sql

CREATE TABLE orchestrator\_memory (

id UUID PRIMARY KEY DEFAULT gen\_random\_uuid(),

tenant\_id VARCHAR(128) NOT NULL,

memory\_type VARCHAR(50) NOT NULL,

\-- \"routing\_pattern\" : intent\_class → agent combination tối ưu

\-- \"plan\_template\" : cấu trúc plan hiệu quả cho loại request cụ thể

\-- \"user\_pattern\" : user/tenant hay kết hợp request kiểu gì

\-- \"routing\_fix\" : correction khi routing sai lần trước

key TEXT NOT NULL,

content TEXT NOT NULL,

confidence FLOAT NOT NULL DEFAULT 0.8,

usage\_count INT NOT NULL DEFAULT 0,

last\_used\_at TIMESTAMPTZ,

created\_at TIMESTAMPTZ NOT NULL DEFAULT now(),

updated\_at TIMESTAMPTZ NOT NULL DEFAULT now()

);

CREATE UNIQUE INDEX idx\_orch\_mem\_key ON
orchestrator\_memory(tenant\_id, memory\_type, key);

CREATE INDEX idx\_orch\_mem\_type ON orchestrator\_memory(tenant\_id,
memory\_type);

\-- Episodic memory: TÁI SỬ DỤNG bảng plans + plan\_sub\_goals đã có.

\-- OrchestratorMemoryService query lại chính lịch sử plans của mình.

**OrchestratorMemoryService --- Retrieve & Store & Learn**

\# orchestrator/core/orchestrator\_memory.py

class OrchestratorMemoryService:

def \_\_init\_\_(self, db: asyncpg.Pool): self.db = db

\# ── Semantic: routing patterns ───────────────────────────────

async def retrieve\_patterns(self, tenant\_id: str, intent\_class: str,

request\_summary: str, limit=4) -\> list\[dict\]:

rows = await self.db.fetch(\"\"\"

SELECT memory\_type, key, content, confidence

FROM orchestrator\_memory

WHERE tenant\_id=\$1 AND (key ILIKE \$2 OR content ILIKE \$3)

ORDER BY confidence DESC, usage\_count DESC LIMIT \$4

\"\"\", tenant\_id, f\"%{intent\_class}%\",
f\"%{request\_summary\[:40\]}%\", limit)

return \[dict(r) for r in rows\]

async def store\_pattern(self, tenant\_id, memory\_type, key, content,
confidence=0.8):

await self.db.execute(\"\"\"

INSERT INTO
orchestrator\_memory(tenant\_id,memory\_type,key,content,confidence)

VALUES(\$1,\$2,\$3,\$4,\$5)

ON CONFLICT(tenant\_id,memory\_type,key)

DO UPDATE SET content=\$4,

confidence=GREATEST(orchestrator\_memory.confidence,\$5),
updated\_at=now()

\"\"\", tenant\_id, memory\_type, key, content, confidence)

\# ── Episodic: tìm plan tương tự trong lịch sử ───────────────

async def find\_similar\_plans(self, tenant\_id: str,

user\_message: str, limit=3) -\> list\[dict\]:

\"\"\"Tái dùng bảng plans --- không cần bảng mới.\"\"\"

rows = await self.db.fetch(\"\"\"

SELECT p.goal, p.user\_message,

json\_agg(sg ORDER BY sg.sequence) AS sub\_goals

FROM plans p

JOIN plan\_sub\_goals sg ON sg.plan\_id = p.id

WHERE p.tenant\_id=\$1 AND p.status=\'completed\'

AND p.user\_message ILIKE \$2

GROUP BY p.id ORDER BY p.created\_at DESC LIMIT \$3

\"\"\", tenant\_id, f\"%{user\_message\[:40\]}%\", limit)

return \[dict(r) for r in rows\]

\# ── Learning: extract routing insights sau plan hoàn thành ───

async def extract\_and\_store(self, tenant\_id: str,

plan: dict, sub\_goals: list):

\"\"\"Gọi sau plans.status = completed. Best-effort, không block
response.\"\"\"

facts = await self.\_call\_extract\_llm(plan, sub\_goals)

for f in facts:

await self.store\_pattern(tenant\_id, f\[\"memory\_type\"\],

f\[\"key\"\], f\[\"content\"\],

f.get(\"confidence\", 0.8))

**Plan Node --- Tích Hợp Memory Trước Và Sau Khi Plan**

ORCHESTRATOR\_SYSTEM\_TEMPLATE = \"\"\"

Bạn là Orchestrator của hệ thống Agentic AI.

{agent\_manifest}

{routing\_memory}

{similar\_plans}

\#\# Routing Rules

\- Chỉ route đến agents có trong \"Available Domain Agents\".

\- Tham khảo \"Routing Patterns\" để chọn agent combination tốt nhất.

\- Tham khảo \"Plan Tương Tự\" để tái dùng cấu trúc đã thành công.

\- LUÔN ưu tiên context hiện tại của user hơn memory nếu có mâu thuẫn.

\"\"\"

class PlanNode:

async def run(self, state) -\> OrchestratorState:

\# Trước plan: retrieve memory context

patterns = await self.memory.retrieve\_patterns(

state.tenant\_id, state.intent, state.user\_message)

similar\_plans = await self.memory.find\_similar\_plans(

state.tenant\_id, state.user\_message)

system = ORCHESTRATOR\_SYSTEM\_TEMPLATE.format(

agent\_manifest = self.registry.build\_prompt\_context(),

routing\_memory = self.\_render\_patterns(patterns),

similar\_plans = self.\_render\_plans(similar\_plans),

)

plan = await llm\_call(messages=\..., system=system,
task\_type=\"plan\")

return state.update(plan=plan)

\# Sau plan completed (callback từ PlanService):

async def on\_plan\_completed(self, tenant\_id, plan, sub\_goals):

await self.memory.extract\_and\_store(tenant\_id, plan, sub\_goals)

**Routing Learning Extraction Prompt**

ROUTING\_LEARNINGS\_PROMPT = \"\"\"

Plan vừa hoàn thành. Extract tối đa 3 routing insights.

Output JSON: \[{memory\_type, key, content, confidence}\]

Các loại insight:

routing\_pattern: \"intent X → agent combo A+B hiệu quả\"

key=intent\_class, content=combo + lý do ngắn gọn

plan\_template: \"request dạng Y → cấu trúc sub\_goals này tối ưu\"

key=request\_pattern, content=template

user\_pattern: \"user hay kết hợp X+Y → nên confirm trước\"

key=behavior, content=pattern + action

routing\_fix: \"routing Z thất bại → thử W\"

key=failure\_pattern, content=correction

Ví dụ:

\[{\"memory\_type\":\"routing\_pattern\",\"key\":\"order+bi\_combined\",

\"content\":\"Request vừa tạo đơn vừa xem BI → chạy BI trước rồi mới
Order\",

\"confidence\":0.85}\]

Nếu không có insight đáng lưu → trả về \[\].

\"\"\"

**Tổng Quan Migration Sequence**

  --------------- ------------------------------- ------------------------------------- ----------------------------------------------------
  **Migration**   **File**                        **Tạo bảng**                          **Dùng bởi**
  **001**         001\_plans.sql                  plans, plan\_sub\_goals               Orchestrator --- lưu plan + link A2A task
  **002**         002\_agent\_memory.sql          agent\_memory, agent\_task\_history   Order Agent, BI Agent --- semantic + episodic
  **003**         003\_orchestrator\_memory.sql   orchestrator\_memory                  Orchestrator --- routing patterns + plan templates
  --------------- ------------------------------- ------------------------------------- ----------------------------------------------------

**1.7 HITL --- Human-in-the-Loop Xác Nhận Tool Nguy Hiểm**

  ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
  **Nguyên tắc:** Tool chia làm 2 nhóm theo mức độ ảnh hưởng. Tool read-only thực thi ngay. Tool mutating (tạo/sửa/xoá dữ liệu) bắt buộc phải hỏi user trước. Câu hỏi \"có cần re-plan không\" được trả lời ở cuối mục này.
  ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------

**Phân Loại Tool --- 2 Nhóm**

  --------------- ---------------------------- ------------------------------------------------------------------------------------------------------ --------------------------------------------------------------------------------------
  **Nhóm**        **requires\_confirmation**   **Ví dụ tool**                                                                                         **Hành động**
  **Read-only**   **false (default)**          get\_customers, get\_products, bi\_\_run\_query, get\_order\_detail                                    Thực thi ngay, không hỏi. Kết quả không thay đổi trạng thái hệ thống.
  **Mutating**    **true**                     order\_\_create\_order, order\_\_update\_order, order\_\_cancel\_order, customer\_\_create\_customer   Pause ReAct loop → sinh confirmation message → chờ user → resume/re-reason/escalate.
  --------------- ---------------------------- ------------------------------------------------------------------------------------------------------ --------------------------------------------------------------------------------------

**HITL Flow --- 3 Nhánh Sau Xác Nhận**

\# ── Trước khi gọi tool --- kiểm tra requires\_confirmation ────────

async def \_execute\_tool(self, tool\_name: str, args: dict,

messages: list, plan\_id: str) -\> dict:

tool\_def = await self.tool\_client.get\_definition(tool\_name)

if not tool\_def.get(\"requires\_confirmation\", False):

return await self.tool\_client.execute(tool\_name, args) \# read-only:
thực thi ngay

\# ── Tool mutating → sinh confirmation message ───────────────

confirm\_msg = await self.\_generate\_confirm\_message(tool\_name, args,
tool\_def)

\# Pause: A2A task chuyển sang input\_required, trả về UI

return {\"\_\_hitl\_\_\": True, \"question\": confirm\_msg,
\"pending\_tool\": tool\_name,

\"pending\_args\": args}

\# ── Xử lý phản hồi user sau HITL ───────────────────────────────

async def resume\_after\_hitl(self, user\_response: str,

pending\_tool: str, pending\_args: dict,

messages: list) -\> dict:

intent = await self.\_classify\_hitl\_response(user\_response)

\# intent: \"confirm\" \| \"modify\" \| \"cancel\" \| \"scope\_change\"

if intent == \"confirm\":

\# Nhánh 1: User đồng ý nguyên vẹn → thực thi tool, tiếp tục loop

result = await self.tool\_client.execute(pending\_tool, pending\_args)

messages.append({\"role\":\"tool\",\"content\": json.dumps(result)})

return await self.\_continue\_react(messages) \# resume loop

elif intent == \"modify\":

\# Nhánh 2: User đồng ý nhưng có chỉnh sửa → inject vào messages

\# ReAct loop tự re-reason với context mới --- KHÔNG cần explicit
re-plan

messages.append({\"role\":\"user\",\"content\": user\_response})

return await self.\_continue\_react(messages) \# loop tự re-reason

elif intent == \"cancel\":

\# Nhánh 3: User từ chối → loop re-reason để tìm alternative hoặc dừng

messages.append({\"role\":\"user\",\"content\":

f\"Người dùng từ chối thực hiện {pending\_tool}. Hãy thông báo và
dừng.\"})

return await self.\_continue\_react(messages)

else: \# scope\_change

\# Nhánh 4: User thay đổi scope hoàn toàn → signal Orchestrator re-plan

return {\"\_\_scope\_change\_\_\": True, \"new\_request\":
user\_response}

**Sinh Confirmation Message --- Rõ Nghĩa Với Người Dùng**

CONFIRM\_PROMPT = \"\"\"

Sinh 1 câu xác nhận ngắn gọn cho hành động sắp thực hiện.

Yêu cầu: rõ ràng, dùng ngôn ngữ nghiệp vụ, nêu rõ tác động.

Format: \"\[Hành động\]. \[Tác động\]. Xác nhận không?\"

Ví dụ:

tool: order\_\_update\_order, args: {order\_id: \"ORD-123\", items:
\[{qty: 3}\]}

→ \"Sửa đơn \#ORD-123: tăng số lượng café đen từ 2 → 3 ly.

Tổng tiền sẽ tăng thêm 25,000đ. Xác nhận không?\"

tool: order\_\_cancel\_order, args: {order\_id: \"ORD-456\"}

→ \"Huỷ đơn \#ORD-456 (Lâm --- 2 món --- 85,000đ).

Đơn đã huỷ không thể khôi phục. Xác nhận không?\"

\"\"\"

async def \_generate\_confirm\_message(self, tool\_name, args,
tool\_def) -\> str:

impact\_hint = tool\_def.get(\"impact\_template\", \"\") \# hint từ
tools.yaml

return await llm\_call(

messages=\[{\"role\":\"user\",\"content\":

f\"tool={tool\_name}\\nargs={json.dumps(args,ensure\_ascii=False)}\\nhint={impact\_hint}\"}\],

system=CONFIRM\_PROMPT,

task\_type=\"confirm\_message\" \# dùng gpt-4o-mini

)

**Trả Lời Câu Hỏi: Có Cần Re-plan Sau HITL Không?**

  ----------------------------------------------------------------------------------------------------
  **Câu trả lời: Không cần explicit re-plan riêng --- ReAct loop chính là cơ chế re-plan tự nhiên.**
  ----------------------------------------------------------------------------------------------------

**A2A Task Status --- HITL States**

  --------------------- ---------------------------- -------------------------------------------------------------------------------------------
  **Trạng thái A2A**    **Trigger**                  **Ý nghĩa**
  **submitted**         POST /a2a/tasks              Task vừa được gửi, chưa bắt đầu xử lý
  **working**           ReAct loop đang chạy         Agent đang thực thi, gọi tools read-only
  **input\_required**   Tool mutating phát hiện      Đang chờ user xác nhận --- ReAct loop paused. Client nhận state này → hiển thị confirm UI
  **completed**         ReAct loop kết thúc          Task xong, result trả về user
  **failed**            Exception hoặc user cancel   Task thất bại, error message trả về
  --------------------- ---------------------------- -------------------------------------------------------------------------------------------

**1.8 Task Dispatch & Dependency Engine --- Orchestrator Giao Task Cho
Agent**

  -----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
  **3 vấn đề cần giải quyết:** (1) Mỗi agent nhận đủ context để tự xử lý đúng intent. (2) Task độc lập chạy song song. (3) Task phụ thuộc chờ upstream xong mới dispatch, kèm kết quả upstream. Orchestrator quản lý toàn bộ dependency graph --- agents không cần biết nhau.
  -----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------

**A2A Task Payload --- Context Đủ Để Agent Tự Xử Lý**

\# Mỗi task gửi đến Domain Agent phải mang đủ 4 nhóm thông tin:

class A2ATaskPayload(BaseModel):

\# ── 1. Identity & Traceability ──────────────────────────────

task\_id: str \# uuid --- dùng để poll A2A status

plan\_id: str \# link về plan của Orchestrator

sub\_goal\_sequence: int \# thứ tự sub\_goal trong plan

session\_id: str

tenant\_id: str

\# ── 2. Intent --- Agent hiểu user muốn gì ────────────────────

original\_message: str \# câu gốc của user, KHÔNG rút gọn

skill: str \# \"create\_order\" \| \"bi\_query\"

instructions: str \# Plan node sinh ra --- mô tả cụ thể task này cần làm
gì

\# bao gồm: scope, điều kiện, output mong đợi

\# ── 3. Conversation context ─────────────────────────────────

conversation\_history: list\[dict\] \# N turns gần nhất (mặc định 6
turns)

\# ── 4. Dependency results ────────────────────────────────────

dependency\_results: dict\[str, dict\] \# agent\_name → result từ
upstream

\# Ví dụ: {\"order-agent\": {\"order\_id\": \"ORD-123\", \"total\":
85000}}

\# Agent downstream dùng để tham chiếu kết quả, không cần gọi lại

**Plan Node Output --- Khai Báo Dependency Graph**

\# Plan node (LLM) phải sinh ra instructions + depends\_on cho từng
step.

\# Đây là thông tin quyết định parallel vs sequential.

\# Ví dụ: user hỏi \"xem doanh thu hôm nay và tạo đơn cho anh Lâm cafe
đen\"

\# → 2 task độc lập → chạy song song

{

\"routing\": {

\"steps\": \[

{

\"sequence\": 1,

\"agent\": \"bi-agent\",

\"skill\": \"bi\_query\",

\"depends\_on\": \[\],

\"instructions\": \"Truy vấn tổng doanh thu ngày hôm nay (chỉ ngày hiện
tại).

Trả về: tổng doanh thu, số đơn, trung bình/đơn.\",

},

{

\"sequence\": 2,

\"agent\": \"order-agent\",

\"skill\": \"create\_order\",

\"depends\_on\": \[\],

\"instructions\": \"Tạo đơn hàng cafe đen cho khách hàng Lâm.

Tìm khách Lâm trước, sau đó tạo đơn sau khi xác nhận.\",

}

\]

}

}

\# Ví dụ: user hỏi \"sau khi tạo đơn xong thì cho tôi xem tổng doanh thu
hôm nay\"

\# → BI phụ thuộc Order → sequential

{

\"routing\": {

\"steps\": \[

{

\"sequence\": 1,

\"agent\": \"order-agent\",

\"skill\": \"create\_order\",

\"depends\_on\": \[\],

\"instructions\": \"Tạo đơn hàng cafe đen cho khách Lâm.\",

},

{

\"sequence\": 2,

\"agent\": \"bi-agent\",

\"skill\": \"bi\_query\",

\"depends\_on\": \[\"order-agent\"\],

\"instructions\": \"Sau khi đơn hàng được tạo xong, truy vấn tổng doanh
thu hôm nay.

Kết quả cần bao gồm cả đơn vừa tạo.\",

}

\]

}

}

**Dispatch Engine --- Parallel & Sequential Tự Động**

\# orchestrator/core/dispatch\_engine.py

class DispatchEngine:

\"\"\"Quản lý dependency graph, dispatch parallel/sequential tự
động.\"\"\"

async def execute(self, steps: list\[dict\], plan\_id: str,

state: OrchestratorState) -\> dict\[str, dict\]:

completed: set\[str\] = set()

results: dict\[str, dict\] = {} \# agent\_name → result

remaining = list(steps)

while remaining:

\# Tìm các step đã đủ dependencies → có thể dispatch ngay

ready = \[

s for s in remaining

if all(dep in results for dep in s.get(\"depends\_on\", \[\]))

\]

if not ready:

raise RuntimeError(\"Circular dependency detected in plan\")

\# Dispatch tất cả ready steps song song

dispatched = await asyncio.gather(\*\[

self.\_dispatch\_one(step, plan\_id, state, results)

for step in ready

\])

\# Lưu kết quả, cập nhật tracking

for step, result in zip(ready, dispatched):

results\[step\[\"agent\"\]\] = result

remaining.remove(step)

return results

async def \_dispatch\_one(self, step: dict, plan\_id: str,

state: OrchestratorState,

results: dict) -\> dict:

\# Build payload --- inject dependency results từ upstream

dep\_results = {dep: results\[dep\] for dep in step.get(\"depends\_on\",
\[\])}

payload = A2ATaskPayload(

task\_id = str(uuid4()),

plan\_id = plan\_id,

sub\_goal\_sequence = step\[\"sequence\"\],

session\_id = state.session\_id,

tenant\_id = state.tenant\_id,

original\_message = state.user\_message, \# luôn truyền câu gốc

skill = step\[\"skill\"\],

instructions = step\[\"instructions\"\], \# Plan node đã viết sẵn

conversation\_history = state.history\[-6:\], \# 6 turns gần nhất

dependency\_results = dep\_results, \# kết quả upstream

)

\# Link task\_id vào plan\_sub\_goals ngay sau dispatch

task\_id = await self.a2a\_client.submit(step\[\"agent\"\], payload)

await self.plan\_service.link\_task(plan\_id, step\[\"sequence\"\],
task\_id)

\# Poll đến khi xong, sync status vào DB

return await self.\_poll\_until\_done(task\_id)

**Domain Agent Dùng Payload Như Thế Nào**

\# Trong Domain Agent ReAct loop --- đọc payload để build system prompt

def build\_agent\_system\_prompt(payload: A2ATaskPayload,
memory\_context: str) -\> str:

sections = \[AGENT\_BASE\_PROMPT\]

\# Luôn đặt instructions ở đầu --- đây là nhiệm vụ cụ thể

sections.append(f\"\"\"

\#\# Nhiệm Vụ Hiện Tại

{payload.instructions}

\#\# Yêu Cầu Gốc Của Người Dùng

\"{payload.original\_message}\"

\"\"\")

\# Inject dependency results nếu có

if payload.dependency\_results:

sections.append(\"\#\# Kết Quả Từ Bước Trước\")

for agent\_name, result in payload.dependency\_results.items():

sections.append(f\"- {agent\_name}: {json.dumps(result,
ensure\_ascii=False)}\")

sections.append(\"Tham chiếu kết quả trên nếu cần, không gọi lại.\")

\# Memory context (semantic + episodic)

if memory\_context:

sections.append(memory\_context)

return \"\\n\\n\".join(sections)

**Instructions Generation --- Plan Node Viết Đủ Context Cho Agent**

\# Prompt hướng dẫn Plan node sinh instructions chất lượng cao cho từng
step

PLAN\_INSTRUCTIONS\_GUIDE = \"\"\"

Với mỗi step trong routing, hãy viết \"instructions\" đủ để agent tự xử
lý

mà không cần đọc lại conversation. Instructions phải bao gồm:

1\. Scope cụ thể: agent cần làm gì (không phải agent khác làm gì)

2\. Điều kiện hoặc ràng buộc nếu có (ví dụ: \"chỉ lấy ngày hôm nay\")

3\. Output mong đợi: trả về gì, dạng nào

4\. Nếu depends\_on không rỗng: đề cập cách dùng kết quả upstream

KHÔNG viết instructions chung chung như \"xử lý yêu cầu của user\".

KHÔNG lặp lại toàn bộ câu gốc --- chỉ extract phần liên quan đến agent
này.

Ví dụ tốt (bi-agent, depends\_on=\[order-agent\]):

\"Sau khi đơn \#ORD vừa tạo xong, truy vấn doanh thu hôm nay bao gồm cả
đơn mới.

Trả về: tổng doanh thu, số đơn, top 3 sản phẩm bán chạy nhất.\"

Ví dụ kém:

\"Truy vấn doanh thu theo yêu cầu của user.\" ← quá chung chung

\"\"\"

**Tóm Tắt --- Orchestrator Đảm Bảo Đúng Intent Qua 3 Cơ Chế**

  ------------------------- ----------------------- ------------------------------------------------------------------------------------
  **Cơ chế**                **Thành phần**          **Đảm bảo gì**
  **original\_message**     A2ATaskPayload          Agent luôn có câu gốc → không suy diễn sai ý user do chỉ đọc instructions
  **instructions**          Plan node (LLM sinh)    Scope rõ ràng cho từng agent --- không làm thừa, không bỏ sót
  **dependency\_results**   DispatchEngine inject   Agent downstream dùng kết quả thực từ upstream --- không cần gọi lại, không đoán
  **depends\_on**           DispatchEngine graph    Parallel khi independent, sequential khi có dependency --- tự động, không hardcode
  ------------------------- ----------------------- ------------------------------------------------------------------------------------

**2. Tool Registry v1 --- Config-Based**

  ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
  **Triết lý v1:** Đơn giản nhất đủ dùng --- tool definition lưu trong YAML, load khi startup, hot-reload khi file thay đổi. Không database, không UI, không lưu token. v2 (DB + web UI) là backlog sau MVP.
  ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------

**2.1 Cấu trúc config/tools.yaml**

\# config/tools.yaml

\# Team B điền url/mapping, Team A điền description --- không ai lưu
token

tools:

\# ── CUSTOMER ───────────────────────────────────────────────

\- name: customer\_\_get\_customers

namespace: customer

description: \>

Tìm khách hàng theo tên hoặc số điện thoại.

Luôn gọi tool này trước khi tạo đơn để lấy customer\_id.

parameters:

type: object

required: \[query\]

properties:

query: { type: string, description: \"Tên hoặc SĐT, ví dụ: Lâm,
0901234567\" }

limit: { type: integer, default: 5 }

api:

method: GET

url: \"\${API\_BASE}/customers\"

params: { query: keyword, limit: pageSize }

response\_path: data

response\_rename: { contactNumber: phone, id: customer\_id }

\- name: customer\_\_create\_customer

namespace: customer

requires\_confirmation: true

impact\_template: \"Tạo khách hàng mới: {name} ({phone}). Dữ liệu sẽ
được lưu vào hệ thống.\"

description: \>

Tạo khách hàng mới. Chỉ dùng sau khi không tìm thấy qua

get\_customers VÀ người dùng đã xác nhận muốn tạo mới.

parameters:

type: object

required: \[name\]

properties:

name: { type: string }

phone: { type: string }

address: { type: string }

api:

method: POST

url: \"\${API\_BASE}/customers\"

body\_mapping: { name: name, phone: contactNumber, address: address }

response\_fields: \[id, name, code\]

\# ── ORDER ──────────────────────────────────────────────────

\- name: order\_\_create\_order

namespace: order

requires\_confirmation: true

impact\_template: \"Tạo đơn hàng cho {customer\_name}: {item\_count}
món, tổng {total}đ.\"

description: \>

Tạo đơn hàng mới. Bắt buộc phải có customer\_id và đã

xác nhận nội dung đơn với người dùng trước khi gọi.

parameters:

type: object

required: \[customer\_id, items\]

properties:

customer\_id: { type: string }

items:

type: array

items:

type: object

required: \[product\_id, quantity, price\]

properties:

product\_id: { type: string }

quantity: { type: integer, minimum: 1 }

price: { type: number }

variant: { type: string }

note: { type: string }

table\_number: { type: string }

discount: { type: number, default: 0 }

api:

method: POST

url: \"\${API\_BASE}/orders\"

body\_mapping:

customer\_id: customerId

items: orderDetails

table\_number: tableNumber

discount: discount

item\_mapping:

product\_id: productId

quantity: quantity

price: price

note: note

response\_fields: \[id, code, statusValue, total\]

\# ── BI ─────────────────────────────────────────────────────

\- name: bi\_\_run\_query

namespace: bi

description: \>

Chạy SQL query trên PostgreSQL analytic DB.

Chỉ SELECT, tự động thêm LIMIT 100 nếu chưa có.

parameters:

type: object

required: \[sql\]

properties:

sql: { type: string, description: \"SQL query chỉ SELECT\" }

limit: { type: integer, default: 100, maximum: 500 }

handler: bi\_query\_handler \# custom handler --- không dùng api block

**2.2 Auth Forwarding --- Không lưu token**

\# tool\_registry/core/auth\_context.py

\# Dùng chung ở TẤT CẢ services (Orchestrator, Agents, Tool Registry)

from contextvars import ContextVar

\_token: ContextVar\[str\] = ContextVar(\"token\", default=\"\")

\_tenant\_id: ContextVar\[str\] = ContextVar(\"tenant\_id\",
default=\"\")

def set\_auth(token: str, tenant\_id: str = \"\"):

\_token.set(token)

\_tenant\_id.set(tenant\_id)

def get\_token() -\> str: return \_token.get()

def get\_tenant\_id() -\> str: return \_tenant\_id.get()

\# ── FastAPI Middleware (mỗi service đều có) ──────────────────

class AuthForwardMiddleware(BaseHTTPMiddleware):

async def dispatch(self, request: Request, call\_next):

token =
request.headers.get(\"Authorization\",\"\").removeprefix(\"Bearer \")

tenant = request.headers.get(\"X-Tenant-Id\", \"\")

if token: set\_auth(token.strip(), tenant)

return await call\_next(request)

\# ── HTTP Adapter trong Tool Registry: đọc từ context ─────────

def \_auth\_headers() -\> dict:

return {

\"Authorization\": f\"Bearer {get\_token()}\",

\"Retailer\": get\_tenant\_id(),

}

\# Không có token trong config, không có token trong YAML.

\# Token sống trong ContextVar của request --- tự xoá sau khi request
xong.

**3. Sprint 1 --- Foundation (Ngày 1--7)**

  ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
  **Mục tiêu:** Dựng xong 4 service chạy được trong Docker Compose; Tool Registry load YAML thành công; Orchestrator phân loại intent đúng ≥ 90%; A2A stub flow hoạt động end-to-end. Demo cuối sprint: gửi text → Orchestrator → stub Domain Agent → response.
  ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------

**3.1 Task Breakdown**

  ---------- --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- ----------- ----------------------------------------------------------------------------------------------------------------------------- -------------
  **Ngày**   **Công việc**                                                                                                                                                                                                               **Owner**   **Deliverable**                                                                                                               **Ưu tiên**
  **1**      Khởi tạo monorepo: pyproject.toml, shared/ lib (auth\_context, llm\_client, models), Makefile, .env.example, pre-commit hooks                                                                                               TL          *Repo clone được, \`make dev\` chạy*                                                                                          **P0**
  **1**      Docker Compose 4 services: orchestrator:8000, tool-registry:8001, order-agent:8002, bi-agent:8003 --- mỗi service /health endpoint                                                                                          SE          *\`docker compose up\` --- 4 services healthy*                                                                                **P0**
  **2**      shared/llm.py: OpenAI async wrapper, select\_model(task\_type) từ config, structured output (response\_format=json\_object), retry logic                                                                                    AI          *llm.py unit test: intent/entity task types*                                                                                  **P0**
  **2**      shared/auth\_context.py + AuthForwardMiddleware --- copy vào tất cả services; unit test: token set/get trong async context                                                                                                  SE          *Auth forward test pass 100%*                                                                                                 **P0**
  **3**      Tool Registry: config\_loader.py đọc tools.yaml → build ToolDefinition + dynamic handler; GET /tools; POST /tools/{name}/execute                                                                                            SE          *3 tools load đúng; curl test get\_customers OK*                                                                              **P0**
  **3**      Tool Registry: HTTP adapter dùng auth\_context.\_auth\_headers(); timeout=10s; retry=2; error mapping tiếng Việt                                                                                                   SE          *Adapter test với mock server*                                                                                                **P0**
  **4**      ToolRegistryClient (shared): get\_openai\_tools(namespace) convert sang OpenAI function format; execute(name, params) forward token qua header                                                                              AI          *Client test: tools load + execute mock tool*                                                                                 **P0**
  **4**      Orchestrator LangGraph: OrchestratorState TypedDict, graph compile với nodes stub, Redis checkpointer, session manager                                                                                                      AI          *Graph compile; state persist qua Redis*                                                                                      **P0**
  **4**      DB setup: Alembic init, migration 001\_plans.sql (bảng plans + plan\_sub\_goals đầy đủ cột incl. a2a\_task\_id); asyncpg pool; alembic upgrade head chạy tự động khi Docker Compose start                                   SE          *\`docker compose up\` → migration tự chạy; PlanService unit test: create\_plan/link\_task/sync\_from\_task/get\_plan pass*   **P0**
  **5**      Intent Classifier node: GPT-4o-mini, system prompt + 8 few-shot (order/bi/chitchat), structured output JSON, test 20 câu tiếng Việt                                                                                         AI          *Accuracy ≥ 90% trên 20 test cases*                                                                                           **P0**
  **5**      A2A Server skeleton cho Order Agent và BI Agent: POST /a2a/tasks, GET /a2a/tasks/{id}, GET /.well-known/agent.json (Agent Card); task status: submitted→working→completed                                                   SE          *Postman test A2A flow; agent card trả đúng JSON*                                                                             **P0**
  **6**      AgentRegistry (Orchestrator): fetch /.well-known/agent.json từ seed URLs khi startup, background refresh mỗi 60s, health tracking, build\_prompt\_context()                                                                 AI          *AgentRegistry test: thêm agent mới → tự xuất hiện trong prompt context sau ≤ 60s*                                            **P0**
  **6**      Orchestrator A2A Client: send\_task\_a2a(agent, skill, params) → submit → poll → return; forward auth header; timeout 30s                                                                                                   AI          *A2A Client test với stub Domain Agent*                                                                                       **P0**
  **6**      DB migration 003\_orchestrator\_memory.sql: bảng orchestrator\_memory; OrchestratorMemoryService (retrieve\_patterns/store\_pattern/find\_similar\_plans/extract\_and\_store)                                               SE          *Migration chạy; OrchestratorMemoryService unit test pass*                                                                    **P1**
  **6**      Plan node (GPT-4o): dual-layer output {display, routing{steps\[{sequence,agent,skill,depends\_on,instructions}\]}}; instructions đủ context cho agent tự xử lý; inject AgentRegistry + OrchestratorMemoryService            AI          *Plan sinh đúng depends\_on; instructions rõ scope; test 3 cases: parallel/sequential/single*                                 **P1**
  **6**      DispatchEngine: dependency graph resolver, asyncio.gather cho parallel steps, inject dependency\_results vào downstream payload, A2ATaskPayload model đầy đủ (original\_message/instructions/history/dependency\_results)   SE          *Test: 2 task parallel chạy đúng; 1 task sequential nhận đúng upstream result*                                                **P0**
  **6**      Plan persistence + learning: PlanService.create\_plan() → link\_task() → sync\_from\_task(); khi plan completed → OrchestratorMemoryService.extract\_and\_store(); GET /plans/{session\_id}/current                         SE          *API plan realtime; memory tích lũy sau mỗi completed plan*                                                                   **P1**
  **7**      E2E integration test: 5 order + 3 bi + 2 chitchat → Orchestrator → A2A → Domain stub → Tool Registry mock → response; tất cả pass                                                                                           All         *pytest e2e pass; demo video 3 phút*                                                                                          **P0**
  **7**      Logging middleware: request\_id, session\_id, intent, model, latency\_ms, tokens --- JSON structured log mọi service                                                                                                        SE          *Log đầy đủ cho mọi request*                                                                                                  **P1**
  ---------- --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- ----------- ----------------------------------------------------------------------------------------------------------------------------- -------------

**4. Sprint 2 --- Core Agents: Order & BI (Ngày 8--14)**

  ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
  **Mục tiêu:** Order Agent xử lý đúng câu tiếng Việt tự nhiên ≥ 90% → tạo đơn thành công. BI Agent trả lời đúng ≥ 80% trên 15 query doanh số/khách hàng/công nợ. Demo cuối sprint: live demo với dữ liệu thực.
  ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------

**4.1 Order Agent --- Thiết kế Reasoning Pipeline**

+----------------------------------------------------------------------+
| **ReAct Loop (GPT-4o-mini + Function Calling):**                     |
|                                                                      |
| receive\_input → \[extract\_entities\] → \[match\_products\] →       |
| \[check\_customer\] → \[preview\] → \[wait\_confirm\] →              |
| \[create\_order\]                                                    |
|                                                                      |
| **Key design:** Mỗi bước là một tool call --- LLM tự quyết định gọi  |
| tool nào tiếp theo. Không hardcode flow. Interrupt tại wait\_confirm |
| để user xác nhận.                                                    |
|                                                                      |
| **Product matching:** FAISS embedding                                |
| (paraphrase-multilingual-MiniLM) → top-3 candidates → nếu score ≥    |
| 0.85 chọn ngay, 0.65--0.85 GPT-4o-mini rerank, \< 0.65 hỏi lại.      |
+----------------------------------------------------------------------+

**4.2 Task Breakdown**

  ---------- ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- ----------- --------------------------------------------------------------------------------------------------- -------------
  **Ngày**   **Công việc**                                                                                                                                                                                                                                         **Owner**   **Deliverable**                                                                                     **Ưu tiên**
  **8**      DB migration 002\_agent\_memory.sql: bảng agent\_memory + agent\_task\_history (schema đầy đủ, indexes); MemoryService (retrieve/store/find\_similar\_tasks/record\_task); unit test 4 methods                                                        SE          *Migration chạy; MemoryService unit test pass*                                                      **P0**
  **8**      Product catalog loader: đọc products từ API → normalize (lowercase, bỏ dấu câu, unicode NFC, alias mapping) → corpus text                                                                                                                    SE          *ProductCatalog load \< 3s, normalize test*                                                         **P0**
  **8**      FAISS embedding index: paraphrase-multilingual-MiniLM-L12-v2, benchmark recall\@3 trên 50 product name variations tiếng Việt                                                                                                                          AI          *recall\@3 ≥ 90% trên test set*                                                                     **P0**
  **9**      ProductMatcher: cosine search → threshold routing → (auto / llm-rerank / ask-user); auto-rebuild index mỗi 30 phút hoặc webhook                                                                                                                       AI          *precision ≥ 85% trên 30 test queries*                                                              **P0**
  **9**      VN text utils: số đếm chữ→số (một→1, mười hai→12\...), honorific strip (anh/chị/em/bác/cô/ông/bà), normalize whitespace, unicode NFC                                                                                                                  AI          *unit test 50 cases, 100% pass*                                                                     **P1**
  **10**     Entity extraction prompt v1: system prompt + 8 few-shot examples đa dạng; structured output OrderEntities JSON; test 30 câu tiếng Việt                                                                                                                AI          *precision/recall ≥ 80% trên 30 inputs*                                                             **P0**
  **10**     Order Agent LangGraph: StateGraph với nodes (extract→match→check\_customer→preview→confirm→submit), interrupt tại confirm, state schema                                                                                                               AI          *Graph compile; manual test 5 scenarios*                                                            **P0**
  **11**     Prompt iteration: phân tích error Day 10, thêm few-shot cho edge cases (bàn số, combo, \"thêm vào đơn cũ\", phương ngữ) lên 12 examples                                                                                                               AI          *precision/recall ≥ 90% trên 50 inputs*                                                             **P0**
  **11**     HITL flow: \_execute\_tool() check requires\_confirmation từ tool def; \_generate\_confirm\_message() dùng gpt-4o-mini + impact\_template; A2A task status input\_required; resume\_after\_hitl() với 4 nhánh (confirm/modify/cancel/scope\_change)   AI          *E2E: gọi order\_\_create\_order → pause → confirm → tạo đơn thành công; modify → re-reason đúng*   **P0**
  **11**     MemoryAwareReActLoop cho Order Agent: tích hợp MemoryService.retrieve() + find\_similar\_tasks() trước ReAct, \_extract\_learnings() sau task; inject memory context vào system prompt                                                                AI          *Test: chạy 2 task giống nhau → task 2 dùng learnings từ task 1*                                    **P1**
  **11**     Order Agent A2A server: xử lý skill \"create\_order\"; async task store; integrate LangGraph subgraph; test multi-turn confirm flow                                                                                                                   SE          *A2A E2E: nhận task → tạo đơn thành công*                                                           **P0**
  **12**     BI Agent: SchemaExplorer (introspect pg\_catalog, table allowlist từ config, business glossary YAML); inject vào system prompt                                                                                                                        SE          *Schema context cho 10 tables analytic DB*                                                          **P0**
  **12**     NL2SQL prompt: system prompt + schema + 6 few-shot (doanh thu, khách hàng, công nợ, top N, time range); test 15 queries điển hình                                                                                                                     AI          *SQL correctness ≥ 80% trên 15 queries*                                                             **P0**
  **13**     BI query executor: asyncpg SELECT-only safety check, LIMIT inject, timeout 10s, error handling; result formatter (text/table/summary)                                                                                                                 SE          *10 queries execute đúng, safety block 5 bad SQL*                                                   **P0**
  **13**     BI Agent A2A server: xử lý skill \"bi\_query\"; integrate NL2SQL + executor; test 20 BI queries (doanh thu, khách hàng, công nợ, tồn kho)                                                                                                             AI          *Pass ≥ 80% trên 20 BI queries*                                                                     **P0**
  **13**     MemoryAwareReActLoop cho BI Agent: tích hợp MemoryService --- retrieve sql\_pattern/glossary\_fix trước NL2SQL; store SQL pattern đúng sau mỗi query thành công; test memory reuse trên repeated queries                                              AI          *Query lần 2 nhanh hơn và đúng hơn lần 1 nhờ memory*                                                **P1**
  **14**     Sprint 2 demo: live demo 5 kịch bản thực tế (order chat x2, BI doanh thu, BI khách hàng, BI công nợ) --- record video                                                                                                                                 All         *Demo video 5 phút, pass ≥ 85% scenarios*                                                           **P0**
  ---------- ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- ----------- --------------------------------------------------------------------------------------------------- -------------

**4.3 Order Agent System Prompt --- Key Structure**

\# Lớp 1 --- Role + Rules (cố định)

Bạn là Order Agent của hệ thống. Tạo đơn hàng từ tiếng Việt tự
nhiên.

Quy trình BẮT BUỘC:

1\. Luôn gọi customer\_\_get\_customers trước để lấy customer\_id

2\. Nếu không tìm thấy → hỏi user, sau đó gọi
customer\_\_create\_customer

3\. Dùng product catalog context để resolve product\_id

4\. Tạo preview đơn → đợi xác nhận \[WAITING\_USER\]

5\. Sau xác nhận → gọi order\_\_create\_order

\# Lớp 2 --- Vietnamese NLP rules

Số đếm: một=1, hai=2, ba=3, bốn=4, năm=5, sáu=6, bảy=7, tám=8, chín=9,
mười=10

Không có số lượng → mặc định = 1

Honorifics: anh/chị/em/bác/cô/chú/ông/bà → bỏ, lấy tên sau

Ví dụ: \"anh Lâm hai trứng lộn\" → customer=\"Lâm\", qty=2,
product=\"trứng lộn\"

\# Lớp 3 --- Few-shot examples (8 examples đa dạng)

\# Input: \"bàn 3 cho tôi 3 bò kho bánh mì\"

\# → table\_number=\"3\", items=\[{product=\"bò kho\", qty=3},
{product=\"bánh mì\", qty=3}\]

\# Lớp 4 --- Dynamic context (per-request)

\# Top 20 sản phẩm phổ biến: {product\_name: product\_id, price}

\# Conversation history (3 turns gần nhất)

**5. Sprint 3 --- Evaluation Framework & Quality (Ngày 15--21)**

  --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
  **Mục tiêu:** Xây dựng eval framework hoàn chỉnh, chạy được từ command line và CI. Golden dataset cho Order + BI. Prompt tuning dựa trên failure analysis. Pass rate ≥ 85% E2E. Demo production-ready.
  --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------

**5.1 Evaluation Framework --- Thiết kế**

+----------------------------------------------------------------------+
| **Nguyên tắc:** Eval phải chạy nhanh (\< 2 phút), reproducible, tích |
| hợp vào CI. Mỗi capability có test suite riêng, scorer phù hợp.      |
| Claude Code đọc failure report và suggest prompt improvements.       |
|                                                                      |
| **Test case format:** YAML --- Team AI viết, review như code.        |
| Scorer: exact\_match \| json\_subset \| contains \| llm\_judge \|    |
| sql\_valid.                                                          |
|                                                                      |
| **Output:** JSON report + Markdown summary → commit vào repo để      |
| track regression qua thời gian.                                      |
+----------------------------------------------------------------------+

**5.2 Test Case Format (YAML)**

\# evals/cases/entity\_extraction.yaml

suite: entity\_extraction

version: \"1.0\"

description: \"Entity extraction từ câu tiếng Việt --- customer + items
+ quantities\"

cases:

\- id: EE-001

input: \"anh Lâm hai trứng lộn một cháo lòng\"

expected:

customer.name: \"Lâm\"

customer.honorific: \"anh\"

items.0.product\_query: \"trứng lộn\"

items.0.quantity: 2

items.1.product\_query: \"cháo lòng\"

items.1.quantity: 1

scorer: json\_subset

tags: \[numbers\_vi, multi\_item\]

\- id: EE-002

input: \"chị Mai 1 phở tái nạm ít nước béo\"

expected:

customer.name: \"Mai\"

items.0.product\_query: \"phở tái nạm\"

items.0.quantity: 1

items.0.notes: \"ít nước béo\"

scorer: json\_subset

tags: \[honorific, note\]

\- id: EE-003

input: \"bàn 5 cho tôi 3 bò kho bánh mì\"

expected:

table\_number: \"5\"

items.0.product\_query: \"bò kho\"

items.0.quantity: 3

scorer: json\_subset

tags: \[table\_number, implicit\_combo\]

\# evals/cases/nl2sql.yaml

suite: nl2sql

version: \"1.0\"

cases:

\- id: SQL-001

input: \"doanh thu hôm nay\"

expected\_sql\_contains: \[\"SUM\", \"orders\", \"CURRENT\_DATE\"\]

scorer: sql\_contains

tags: \[revenue, today\]

\- id: SQL-002

input: \"top 5 khách hàng mua nhiều nhất tháng này\"

expected\_sql\_contains: \[\"customers\", \"ORDER BY\", \"LIMIT 5\",
\"date\_trunc\"\]

scorer: sql\_contains

tags: \[customer, top\_n, monthly\]

\- id: SQL-003

input: \"danh sách khách hàng còn công nợ trên 1 triệu\"

expected\_sql\_contains: \[\"debt\", \"1000000\", \"WHERE\"\]

scorer: sql\_contains

tags: \[debt, filter\]

\- id: SQL-004

input: \"doanh thu theo từng ngày trong tuần này\"

expected\_sql\_contains: \[\"GROUP BY\", \"date\", \"week\"\]

scorer: sql\_contains

tags: \[revenue, group\_by, weekly\]

**5.3 Eval Runner**

\# evals/runner.py

import yaml, asyncio, json

from pathlib import Path

from dataclasses import dataclass

\@dataclass

class CaseResult:

id: str

passed: bool

score: float

actual: dict

expected: dict

error: str \| None = None

class EvalRunner:

def \_\_init\_\_(self, target\_url: str):

self.url = target\_url

async def run\_suite(self, yaml\_path: str) -\> dict:

cfg = yaml.safe\_load(Path(yaml\_path).read\_text())

results = \[\]

for case in cfg\[\"cases\"\]:

result = await self.\_run\_case(case, cfg\[\"suite\"\])

results.append(result)

status = \"PASS\" if result.passed else \"FAIL\"

print(f\" \[{status}\] {case\[\'id\'\]}\")

passed = sum(1 for r in results if r.passed)

report = {

\"suite\": cfg\[\"suite\"\],

\"total\": len(results),

\"passed\": passed,

\"pass\_rate\": round(passed / len(results), 3),

\"cases\": \[r.\_\_dict\_\_ for r in results\],

}

return report

async def \_run\_case(self, case: dict, suite: str) -\> CaseResult:

try:

actual = await self.\_call\_agent(suite, case\[\"input\"\])

score = self.\_score(actual, case.get(\"expected\",{}),

case.get(\"expected\_sql\_contains\",\[\]),

case.get(\"scorer\",\"json\_subset\"))

return CaseResult(case\[\"id\"\], score \>= 0.8, score, actual,
case.get(\"expected\",{}))

except Exception as e:

return CaseResult(case\[\"id\"\], False, 0.0, {}, {}, str(e))

def \_score(self, actual, expected, sql\_contains, scorer) -\> float:

if scorer == \"json\_subset\": return \_json\_subset\_score(actual,
expected)

if scorer == \"sql\_contains\": return \_sql\_contains\_score(actual,
sql\_contains)

if scorer == \"exact\_match\": return 1.0 if actual == expected else 0.0

if scorer == \"llm\_judge\": return \_llm\_judge(actual, expected) \#
async

return 0.0

\# ── CLI ───────────────────────────────────────────────────────

if \_\_name\_\_ == \"\_\_main\_\_\":

import sys

suite\_path = sys.argv\[1\] \# python evals/runner.py
evals/cases/nl2sql.yaml

report =
asyncio.run(EvalRunner(\"http://localhost:8000\").run\_suite(suite\_path))

print(f\"\\nResult: {report\[\'passed\'\]}/{report\[\'total\'\]}
({report\[\'pass\_rate\'\]\*100:.0f}%)\")

Path(\"evals/reports/latest.json\").write\_text(json.dumps(report,
indent=2))

**5.4 Task Breakdown**

  ---------- ------------------------------------------------------------------------------------------------------------------------------------------------------ ----------- ----------------------------------------- -------------
  **Ngày**   **Công việc**                                                                                                                                          **Owner**   **Deliverable**                           **Ưu tiên**
  **15**     Eval harness: runner.py, scorers (json\_subset, sql\_contains, exact\_match), CaseResult dataclass, CLI entrypoint, report writer JSON + Markdown      AI          *runner.py chạy được với mock agent*      **P0**
  **15**     Test case YAML cho entity extraction: 20 cases (số đếm, honorifics, combo, bàn số, notes, thêm vào đơn cũ) --- manual label expected output            AI          *20 cases YAML, reviewed by team*         **P0**
  **16**     Test case YAML cho NL2SQL: 20 queries (doanh thu, top khách hàng, công nợ, tồn kho, time range, group by) --- manual label expected SQL fragments      AI          *20 cases YAML, reviewed by team*         **P0**
  **16**     Test case YAML cho intent classification và E2E order flow (10 cases mỗi loại); golden dataset commit vào repo                                         AI          *60 total golden test cases*              **P1**
  **17**     Chạy full eval suite trên current system, generate failure report; phân tích top 5 failure modes cho mỗi suite                                         All         *Failure report + failure analysis doc*   **P0**
  **17**     Prompt tuning Round 1: dựa vào failure analysis, thêm few-shot, điều chỉnh instruction, cập nhật VN utils. Chạy lại eval so sánh.                      AI          *Pass rate tăng ≥ 5% so với baseline*     **P0**
  **18**     Prompt tuning Round 2: focus on bottom 20% failures. Điều chỉnh product matching threshold, FAISS index rebuild, thêm alias dict.                      AI          *Entity extraction ≥ 90%; NL2SQL ≥ 80%*   **P0**
  **18**     Latency optimization: parallel tool calls (asyncio.gather), Redis catalog cache, async throughout; đo P95 trước/sau                                    SE          *P95 Order \< 3s; P95 BI \< 5s*           **P1**
  **19**     Error handling toàn hệ thống: LLM timeout fallback, product not found message, SQL error user-friendly, API error passthrough                          SE          *Mọi error path trả message tiếng Việt*   **P1**
  **19**     Makefile eval targets: \`make eval-order\`, \`make eval-bi\`, \`make eval-all\`; GitHub Actions CI: chạy eval trên PR, fail nếu pass rate giảm \> 5%   TL          *CI pipeline chạy được*                   **P1**
  **20**     Demo UI: HTML/JS đơn giản --- text input + conversation history + order preview card; không cần framework                                              SE          *Demo UI chạy được trên localhost*        **P1**
  **20**     Documentation: README setup, API docs, prompt engineering decisions, tool\_config guide, eval guide                                                    TL          *README đầy đủ*                           **P2**
  **21**     Final eval run: toàn bộ 60+ test cases; target pass rate E2E ≥ 85%. Ghi nhận kết quả vào reports/sprint3\_final.json                                   All         *Pass rate report chính thức*             **P0**
  **21**     Demo chuẩn bị: 5 kịch bản thực tế (order chat x2, BI doanh thu, BI khách hàng, BI công nợ), rehearsal, record video                                    All         *Demo video 7 phút + slide 5 trang*       **P0**
  ---------- ------------------------------------------------------------------------------------------------------------------------------------------------------ ----------- ----------------------------------------- -------------

**6. Claude Code --- Workflow Tích Hợp**

Claude Code không chỉ sinh code --- đóng vai trò reviewer, eval analyst,
và prompt engineer trong suốt quá trình phát triển:

**6.1 Daily Development Workflow**

  ------------------------- -------------------------------------------------------------------------- --------------------------------------------------------------------------------
  **Thời điểm**             **Claude Code làm gì**                                                     **Ví dụ command**
  **Trước khi code**        Review architecture decision, suggest approach, check for edge cases       \"Review approach cho product matching: FAISS vs BM25 với Vietnamese\"
  **Trong khi code**        Generate boilerplate, complete functions, write unit tests, review logic   \"Write pytest for ProductMatcher với 5 edge cases tiếng Việt\"
  **Sau khi viết prompt**   Critique prompt, suggest more few-shot examples, identify blind spots      \"Review system prompt này, tìm cases tiếng Việt mà nó sẽ fail\"
  **Sau khi chạy eval**     Đọc failure report, phân tích pattern, suggest prompt fixes                \"Đây là 8 failed cases. Tìm root cause và suggest prompt improvements\"
  **Trước khi merge PR**    Code review: security, error handling, test coverage, performance          \"Review diff này: check auth forwarding đúng không, có race condition không\"
  **Khi bị stuck**          Debug, trace reasoning, suggest alternative approaches                     \"LangGraph interrupt không resume được sau Redis checkpoint. Debug?\"
  ------------------------- -------------------------------------------------------------------------- --------------------------------------------------------------------------------

**6.2 Eval-Driven Prompt Improvement với Claude Code**

\# Workflow: chạy eval → đưa failure report cho Claude Code → improve
prompt

\# Bước 1: chạy eval, lấy failures

\$ make eval-order

\> entity\_extraction: 17/20 passed (85%)

\> FAIL EE-007: \"bàn 4 hai hủ tiếu\" → table\_number missing

\> FAIL EE-013: \"ba đen\" → quantity=3, product=\"đen\" (lẽ ra \"cafe
đen\")

\> FAIL EE-019: \"anh Long thêm một bún bò vào\" → intent=\"add\" không
nhận ra

\# Bước 2: paste vào Claude Code

\> \"Đây là 3 failed cases của entity extraction prompt.

\> Tìm root cause và suggest cụ thể: thêm few-shot nào,

\> sửa instruction nào để fix 3 cases này mà không break 17 cases đang
pass\"

\# Bước 3: Claude Code suggests

\> Root cause EE-007: table\_number rule chưa có trong system prompt

\> Fix: thêm instruction \"bàn X → table\_number=X\"

\> + thêm few-shot: \"bàn 4 hai hủ tiếu\" → {table:4, qty:2,
product:\"hủ tiếu\"}

\> Root cause EE-013: \"ba\" bị parse thành quantity, thiếu grounding
với catalog

\> Fix: inject top 20 product names vào prompt context

\> Root cause EE-019: \"thêm vào\" intent chưa có trong few-shot

\> Fix: thêm example add\_to\_existing với từ khóa \"thêm\", \"thêm vào
đơn cũ\"

\# Bước 4: apply fixes → chạy lại eval → verify improvement

\$ make eval-order

\> entity\_extraction: 20/20 passed (100%)

**6.3 Useful Claude Code Commands cho Project này**

  ------------------------------- ---------------------------------------------------------------------------------------------------------------------------------------------------------------------
  **Task**                        **Prompt mẫu cho Claude Code**
  **Generate test cases**         \"Tạo 10 YAML test cases cho entity\_extraction, bao phủ: honorifics, số đếm chữ, multi-item, bàn số, notes (ít đường, thêm đá). Follow format trong evals/cases/\"
  **Analyze eval failures**       \"Đây là failure report JSON. Cluster failures theo pattern, identify top 3 root causes, suggest concrete prompt changes với before/after example\"
  **Review A2A implementation**   \"Review a2a\_client.py: check race condition trong polling loop, timeout handling, error propagation. Suggest improvements\"
  **Write NL2SQL few-shot**       \"Viết 5 few-shot examples NL2SQL cho: công nợ, doanh thu theo ngày, top sản phẩm. Input tiếng Việt, output SQL cho PostgreSQL schema này: \[schema\]\"
  **Debug LangGraph state**       \"Order Agent bị stuck ở confirm node sau 2 lần user reply. Đây là state dump và graph code. Tìm bug trong interrupt/resume logic\"
  **Optimize FAISS search**       \"Recall\@1 của product matching chỉ đạt 72%. Đây là 10 failure cases. Suggest: thêm normalization nào, dùng embedding model khác, hay cần re-rank?\"
  ------------------------------- ---------------------------------------------------------------------------------------------------------------------------------------------------------------------

**7. Definition of Done & Success Metrics**

**7.1 Acceptance Criteria MVP**

  -------- ----------------------------------------------- -------------------- ----------------------------
  **\#**   **Tiêu chí**                                    **Target**           **Đo bằng**
  **1**    Intent classification accuracy                  **≥ 90%**            20 test cases eval suite
  **2**    Entity extraction (Order NLP)                   **≥ 90%**            20 YAML test cases
  **3**    Product matching recall\@1                      **≥ 85%**            30 product name variations
  **4**    NL2SQL correctness (Order, BI)                  **≥ 80%**            20 query test cases
  **5**    E2E Order creation success rate                 **≥ 85%**            20 e2e test cases
  **6**    E2E BI query success rate                       **≥ 80%**            20 BI query test cases
  **7**    Latency P95 --- Order chat                      **\< 3 giây**        30 requests, concurrent=1
  **8**    Latency P95 --- BI query                        **\< 5 giây**        20 queries, concurrent=1
  **9**    Auth token không bao giờ được lưu hoặc log      **100% compliant**   Code review + log audit
  **10**   Eval CI pipeline chạy trên mỗi PR               **Pass**             GitHub Actions log
  **11**   Live demo 5 kịch bản không có unhandled error   **0 crash**          Demo video
  -------- ----------------------------------------------- -------------------- ----------------------------

**7.2 Post-MVP Backlog**

  ------------ -------------- ---------------------------------------------------------------------------- -------------------------
  **Sprint**   **Timeline**   **Feature**                                                                  **Phụ thuộc**
  **v1.1**     Tuần 4--5      Tool Registry v2: PostgreSQL + Admin Web UI, version history, audit log      MVP stable ≥ 2 tuần
  **v1.2**     Tuần 6--7      Inventory Agent (tồn kho, cảnh báo thiếu hàng), Multi-turn BI conversation   Tool Registry v2
  **v2.0**     Tuần 8--10     HITL Gateway, Campaign Agent, Model Router nâng cao, monitoring dashboard    v1.2 + production infra
  ------------ -------------- ---------------------------------------------------------------------------- -------------------------
