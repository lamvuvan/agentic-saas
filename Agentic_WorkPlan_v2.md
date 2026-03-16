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

Hệ thống gồm 4 service độc lập, giao tiếp qua A2A và MCP, deploy bằng
Docker Compose:

  ------------------- ---------- -------------- -------------------------------------------------------------------------------------------------------------------------------------
  **Service**         **Port**   **Protocol**   **Trách nhiệm**
  **Orchestrator**    8000       REST / WS      Nhận request, phân loại intent (GPT-4o-mini), lập plan (GPT-4o), giao task cho Domain Agent qua A2A, tổng hợp response
  **Tool Registry**   8001       MCP (HTTP)     Catalog tool từ YAML config; GET /tools · POST /tools/{name}/execute. Forward Bearer token từ header --- không lưu trữ.
  **Order Agent**     8002       A2A + MCP      A2A server nhận task; tự reasoning (ReAct loop GPT-4o-mini); gọi Tool Registry để search product, get/create customer, create order
  **BI Agent**        8003       A2A + MCP      A2A server nhận task; tự reasoning NL2SQL (GPT-4o); gọi Tool Registry để execute query PostgreSQL, format kết quả
  ------------------- ---------- -------------- -------------------------------------------------------------------------------------------------------------------------------------

**1.1 Communication Flow**

+----------------------------------------------------------------------+
| **User → Orchestrator:** HTTP POST /chat \| WebSocket /chat/stream   |
| (text input --- client xử lý STT trước khi gửi)                      |
|                                                                      |
| **Orchestrator → Domain Agent:** A2A --- POST /a2a/tasks + GET       |
| /a2a/tasks/{id} (poll)                                               |
|                                                                      |
| **Domain Agent → Tool Registry:** MCP --- GET /tools?namespace=X +   |
| POST /tools/{name}/execute                                           |
|                                                                      |
| **Auth:** Bearer token forward qua HTTP header xuyên suốt chuỗi ---  |
| không lưu, không biến đổi                                            |
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

Bạn là Orchestrator của hệ thống Agentic SaaS.

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

  ---------- ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------- ----------- ------------------------------------------------------------------------------------ -------------
  **Ngày**   **Công việc**                                                                                                                                                                **Owner**   **Deliverable**                                                                      **Ưu tiên**
  **1**      Khởi tạo monorepo: pyproject.toml, shared/ lib (auth\_context, llm\_client, models), Makefile, .env.example, pre-commit hooks                                                TL          *Repo clone được, \`make dev\` chạy*                                                 **P0**
  **1**      Docker Compose 4 services: orchestrator:8000, tool-registry:8001, order-agent:8002, bi-agent:8003 --- mỗi service /health endpoint                                           SE          *\`docker compose up\` --- 4 services healthy*                                       **P0**
  **2**      shared/llm.py: OpenAI async wrapper, select\_model(task\_type) từ config, structured output (response\_format=json\_object), retry logic                                     AI          *llm.py unit test: intent/entity task types*                                         **P0**
  **2**      shared/auth\_context.py + AuthForwardMiddleware --- copy vào tất cả services; unit test: token set/get trong async context                                                   SE          *Auth forward test pass 100%*                                                        **P0**
  **3**      Tool Registry: config\_loader.py đọc tools.yaml → build ToolDefinition + dynamic handler; GET /tools; POST /tools/{name}/execute                                             SE          *3 tools load đúng; curl test get\_customers OK*                                     **P0**
  **3**      Tool Registry: HTTP adapter dùng auth\_context.\_auth\_headers(); timeout=10s; retry=2; error mapping tiếng Việt                                                    SE          *Adapter test với mock server*                                                       **P0**
  **4**      ToolRegistryClient (shared): get\_openai\_tools(namespace) convert sang OpenAI function format; execute(name, params) forward token qua header                               AI          *Client test: tools load + execute mock tool*                                        **P0**
  **4**      Orchestrator LangGraph: OrchestratorState TypedDict, graph compile với nodes stub, Redis checkpointer, session manager                                                       AI          *Graph compile; state persist qua Redis*                                             **P0**
  **5**      Intent Classifier node: GPT-4o-mini, system prompt + 8 few-shot (order/bi/chitchat), structured output JSON, test 20 câu tiếng Việt                                          AI          *Accuracy ≥ 90% trên 20 test cases*                                                  **P0**
  **5**      A2A Server skeleton cho Order Agent và BI Agent: POST /a2a/tasks, GET /a2a/tasks/{id}, GET /.well-known/agent.json (Agent Card); task status: submitted→working→completed    SE          *Postman test A2A flow; agent card trả đúng JSON*                                    **P0**
  **6**      AgentRegistry (Orchestrator): fetch /.well-known/agent.json từ seed URLs khi startup, background refresh mỗi 60s, health tracking, build\_prompt\_context()                  AI          *AgentRegistry test: thêm agent mới → tự xuất hiện trong prompt context sau ≤ 60s*   **P0**
  **6**      Orchestrator A2A Client: send\_task\_a2a(agent, skill, params) → submit → poll → return; forward auth header; timeout 30s                                                    AI          *A2A Client test với stub Domain Agent*                                              **P0**
  **6**      Plan node (GPT-4o): inject AgentRegistry.build\_prompt\_context() vào system prompt, JSON output {reasoning, steps\[\]}, inject 3 turns history; KHÔNG hardcode agent list   AI          *Plan node route đúng sau khi thêm/xoá agent dynamically*                            **P1**
  **7**      E2E integration test: 5 order + 3 bi + 2 chitchat → Orchestrator → A2A → Domain stub → Tool Registry mock → response; tất cả pass                                            All         *pytest e2e pass; demo video 3 phút*                                                 **P0**
  **7**      Logging middleware: request\_id, session\_id, intent, model, latency\_ms, tokens --- JSON structured log mọi service                                                         SE          *Log đầy đủ cho mọi request*                                                         **P1**
  ---------- ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------- ----------- ------------------------------------------------------------------------------------ -------------

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

  ---------- ------------------------------------------------------------------------------------------------------------------------------------------- ----------- --------------------------------------------------- -------------
  **Ngày**   **Công việc**                                                                                                                               **Owner**   **Deliverable**                                     **Ưu tiên**
  **8**      Product catalog loader: đọc products từ API → normalize (lowercase, bỏ dấu câu, unicode NFC, alias mapping) → corpus text          SE          *ProductCatalog load \< 3s, normalize test*         **P0**
  **8**      FAISS embedding index: paraphrase-multilingual-MiniLM-L12-v2, benchmark recall\@3 trên 50 product name variations tiếng Việt                AI          *recall\@3 ≥ 90% trên test set*                     **P0**
  **9**      ProductMatcher: cosine search → threshold routing → (auto / llm-rerank / ask-user); auto-rebuild index mỗi 30 phút hoặc webhook             AI          *precision ≥ 85% trên 30 test queries*              **P0**
  **9**      VN text utils: số đếm chữ→số (một→1, mười hai→12\...), honorific strip (anh/chị/em/bác/cô/ông/bà), normalize whitespace, unicode NFC        AI          *unit test 50 cases, 100% pass*                     **P1**
  **10**     Entity extraction prompt v1: system prompt + 8 few-shot examples đa dạng; structured output OrderEntities JSON; test 30 câu tiếng Việt      AI          *precision/recall ≥ 80% trên 30 inputs*             **P0**
  **10**     Order Agent LangGraph: StateGraph với nodes (extract→match→check\_customer→preview→confirm→submit), interrupt tại confirm, state schema     AI          *Graph compile; manual test 5 scenarios*            **P0**
  **11**     Prompt iteration: phân tích error Day 10, thêm few-shot cho edge cases (bàn số, combo, \"thêm vào đơn cũ\", phương ngữ) lên 12 examples     AI          *precision/recall ≥ 90% trên 50 inputs*             **P0**
  **11**     Order Agent A2A server: xử lý skill \"create\_order\"; async task store; integrate LangGraph subgraph; test multi-turn confirm flow         SE          *A2A E2E: nhận task → tạo đơn thành công*           **P0**
  **12**     BI Agent: SchemaExplorer (introspect pg\_catalog, table allowlist từ config, business glossary YAML); inject vào system prompt              SE          *Schema context cho 10 tables analytic DB*          **P0**
  **12**     NL2SQL prompt: system prompt + schema + 6 few-shot (doanh thu, khách hàng, công nợ, top N, time range); test 15 queries điển hình           AI          *SQL correctness ≥ 80% trên 15 queries*             **P0**
  **13**     BI query executor: asyncpg SELECT-only safety check, LIMIT inject, timeout 10s, error handling; result formatter (text/table/summary)       SE          *10 queries execute đúng, safety block 5 bad SQL*   **P0**
  **13**     BI Agent A2A server: xử lý skill \"bi\_query\"; integrate NL2SQL + executor; test 20 BI queries (doanh thu, khách hàng, công nợ, tồn kho)   AI          *Pass ≥ 80% trên 20 BI queries*                     **P0**
  **14**     Sprint 2 demo: live demo 5 kịch bản thực tế (order chat x2, BI doanh thu, BI khách hàng, BI công nợ) --- record video                       All         *Demo video 5 phút, pass ≥ 85% scenarios*           **P0**
  ---------- ------------------------------------------------------------------------------------------------------------------------------------------- ----------- --------------------------------------------------- -------------

**4.3 Order Agent System Prompt --- Key Structure**

\# Lớp 1 --- Role + Rules (cố định)

Bạn là Order Agent của hệ thống Agentic SaaS. Tạo đơn hàng từ tiếng Việt tự
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
