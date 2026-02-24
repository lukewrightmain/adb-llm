#!/usr/bin/env bash
# Benchmark different prompt styles against a running cellswarm-master
# Usage: bash scripts/bench_prompts.sh [host:port] [n_predict] [seed]

set -euo pipefail

HOST="${1:-10.105.0.41:8080}"
N_PREDICT="${2:-128}"
SEED="${3:-42}"

# Define prompts with categories
declare -a CATEGORIES=(
    "Code: Python fibonacci"
    "Code: JavaScript async"
    "Code: SQL query"
    "Reasoning: Math word problem"
    "Reasoning: Logic puzzle"
    "Creative: Short story"
    "Creative: Poetry"
    "Factual: Science explanation"
    "Factual: History question"
    "Instruction: Step-by-step"
)

declare -a PROMPTS=(
    "Write a Python function that computes the Fibonacci sequence efficiently using dynamic programming."
    "Write a JavaScript async function that fetches data from multiple APIs in parallel and returns the combined results."
    "Write a SQL query that finds the top 5 customers by total order value, including their name, email, and total spent."
    "A train travels from city A to city B at 60 mph. Another train leaves city B toward city A at 80 mph. If the cities are 280 miles apart, how long until they meet? Show your work."
    "There are three boxes. One contains only apples, one contains only oranges, and one contains both. All labels are wrong. You can pick one fruit from one box. How do you determine the correct labels?"
    "Write a short story about a robot that discovers it can dream. Keep it under 200 words."
    "Write a haiku about the ocean at sunset, followed by a limerick about a programmer debugging code."
    "Explain how photosynthesis works at a molecular level. Include the light-dependent and light-independent reactions."
    "What were the main causes and consequences of the Industrial Revolution in 18th century England?"
    "Give me step-by-step instructions for setting up a basic CI/CD pipeline using GitHub Actions for a Node.js project."
)

echo "============================================"
echo " PROMPT STYLE BENCHMARK"
echo "============================================"
echo "  Host:       $HOST"
echo "  n_predict:  $N_PREDICT"
echo "  Seed:       $SEED"
echo "  Prompts:    ${#PROMPTS[@]}"
echo ""

# Results arrays
declare -a RESULTS=()

for i in "${!PROMPTS[@]}"; do
    category="${CATEGORIES[$i]}"
    prompt="${PROMPTS[$i]}"

    echo "[$((i+1))/${#PROMPTS[@]}] $category"
    echo "  Prompt: ${prompt:0:80}..."

    start_time=$(date +%s%N)

    # Send request
    response=$(curl -s -X POST "http://${HOST}/v1/chat/completions" \
        -H "Content-Type: application/json" \
        -d "$(jq -n \
            --arg prompt "$prompt" \
            --argjson n_predict "$N_PREDICT" \
            --argjson seed "$SEED" \
            '{messages:[{role:"user",content:$prompt}],stream:false,n_predict:$n_predict,seed:$seed}')" \
        --max-time 300 2>/dev/null) || response=""

    end_time=$(date +%s%N)
    wall_ms=$(( (end_time - start_time) / 1000000 ))

    if [ -z "$response" ]; then
        echo "  FAILED (no response)"
        RESULTS+=("$category|FAIL|0|0|0|0")
        continue
    fi

    # Extract content and token counts
    content=$(echo "$response" | jq -r '.choices[0].message.content // "ERROR"' 2>/dev/null)
    completion_tokens=$(echo "$response" | jq -r '.usage.completion_tokens // 0' 2>/dev/null)
    prompt_tokens=$(echo "$response" | jq -r '.usage.prompt_tokens // 0' 2>/dev/null)
    total_tokens=$(echo "$response" | jq -r '.usage.total_tokens // 0' 2>/dev/null)

    if [ "$completion_tokens" -gt 0 ] 2>/dev/null; then
        tok_per_sec=$(echo "scale=2; $completion_tokens * 1000 / $wall_ms" | bc 2>/dev/null || echo "0")
    else
        tok_per_sec="0"
    fi

    # Get spec metrics from the log
    spec_info=$(~/.local/bin/adb -s ${HOST%%:*}:5555 shell "tail -50 /data/local/tmp/cellswarm-worker.log" 2>/dev/null | grep "SPEC-ACCEPT" | tail -5)
    total_accepted=0
    total_drafted=0
    n_cycles=0
    while IFS= read -r line; do
        if [[ "$line" =~ accepted=([0-9]+)/([0-9]+) ]]; then
            total_accepted=$((total_accepted + ${BASH_REMATCH[1]}))
            total_drafted=$((total_drafted + ${BASH_REMATCH[2]}))
            n_cycles=$((n_cycles + 1))
        fi
    done <<< "$spec_info"

    if [ "$total_drafted" -gt 0 ]; then
        accept_pct=$(echo "scale=1; $total_accepted * 100 / $total_drafted" | bc 2>/dev/null || echo "0")
    else
        accept_pct="0"
    fi

    echo "  Tokens: ${completion_tokens} gen / ${prompt_tokens} prompt"
    echo "  Wall:   ${wall_ms}ms | ${tok_per_sec} tok/s"
    echo "  Spec:   ${total_accepted}/${total_drafted} accepted (${accept_pct}%) in ${n_cycles} cycles"
    echo "  Output: ${content:0:100}..."
    echo ""

    RESULTS+=("$category|${tok_per_sec}|${completion_tokens}|${wall_ms}|${accept_pct}|${n_cycles}")

    # Small pause between requests
    sleep 2
done

echo ""
echo "============================================"
echo " RESULTS SUMMARY"
echo "============================================"
printf "%-30s %8s %6s %8s %8s %6s\n" "Category" "tok/s" "tokens" "wall_ms" "accept%" "cycles"
printf "%-30s %8s %6s %8s %8s %6s\n" "------------------------------" "--------" "------" "--------" "--------" "------"

for result in "${RESULTS[@]}"; do
    IFS='|' read -r cat tps toks wall acc cyc <<< "$result"
    printf "%-30s %8s %6s %8s %7s%% %6s\n" "$cat" "$tps" "$toks" "$wall" "$acc" "$cyc"
done

echo ""
echo "Done."
