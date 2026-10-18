# fintechathon-hackathon-shenda

## Run

```sh
cp .env.example .env      # then fill in DASHSCOPE_API_KEY
docker compose up --build
```

Open http://localhost:8080 and log in as `liming` or `hanmei` (password `demo1234`).

Services: web Channel `:8080`, Agent `:8001` ([agent/README.md](agent/README.md)), Bank `:8000` ([bank/README.md](bank/README.md)).

To work on the web Channel (`web/`, React + Vite) against a running Bank and Agent: `cd web && npm install && npm run dev`. The dev server proxies `/bank` to `:8000` and `/agent` to `:8001`, as nginx does in Compose. The Channel only displays events ([docs/conversation-events.md](docs/conversation-events.md)), so it is checked by hand.
