from __future__ import annotations
import json, time
from typing import Any, Sequence
import httpx
from tenacity import AsyncRetrying, retry_if_exception_type, stop_after_attempt, wait_random_exponential
from schemasift.exceptions import ProviderError
from schemasift.models import CandidateDecision, Classification, Probabilities
from .base import Candidate, ProviderBatchResult, ProviderCapabilities

class _Transient(Exception): pass

class OpenAICompatibleProvider:
    adapter = "openai_compatible"
    capabilities = ProviderCapabilities(maximum_questions=5000)
    def __init__(self, *, name: str, base_url: str, model: str, api_key: str|None=None,
                 endpoint: str="/chat/completions", auth_header: str="Authorization",
                 auth_scheme: str="Bearer", timeout_ms: int=10000, max_retries: int=2,
                 reasoning_effort: str|None=None, structured_output: bool=True,
                 input_cost_per_million: float|None=None, output_cost_per_million: float|None=None,
                 client: httpx.AsyncClient|None=None):
        self.name,self.model=name,model; self.base_url=base_url.rstrip("/"); self.endpoint=endpoint
        self.api_key,self.auth_header,self.auth_scheme=api_key,auth_header,auth_scheme
        self.max_retries=max_retries; self.reasoning_effort=reasoning_effort
        self.structured_output=structured_output; self.input_rate=input_cost_per_million
        self.output_rate=output_cost_per_million; self._owns_client=client is None
        self.client=client or httpx.AsyncClient(timeout=timeout_ms/1000)
    def _headers(self):
        return {self.auth_header:f"{self.auth_scheme} {self.api_key}".strip()} if self.api_key else {}
    async def classify(self, *, question: str, evidence: str|None, candidates: Sequence[Candidate], context: dict[str,Any]):
        prompt={"question":question,"evidence":evidence or "","context":context,"candidates":[{"candidate_id":c.id,"kind":c.kind,"name":c.name,"metadata":c.metadata} for c in candidates]}
        data,response,latency=await self._complete("Classify every candidate as DIRECT, POSSIBLE, or UNLIKELY. Optimize for recall. Return JSON: {decisions:[{candidate_id,classification,probabilities:{direct,possible,unlikely},confidence}]}. Return every candidate exactly once.",prompt)
        decisions=[CandidateDecision(candidate_id=x["candidate_id"],classification=Classification(x["classification"].upper()),probabilities=Probabilities(**x["probabilities"]),confidence=x.get("confidence")) for x in data["decisions"]]
        return self._result(decisions,response,latency)
    async def classify_roles(self, *, question: str, evidence: str|None, candidates: Sequence[Candidate], roles: Sequence[str], context: dict[str,Any]):
        prompt={"question":question,"evidence":evidence or "","roles":list(roles),"candidates":[{"candidate_id":c.id,"name":c.name,"metadata":c.metadata} for c in candidates]}
        data,response,latency=await self._complete("Return JSON {decisions:[{candidate_id,roles:{ROLE:probability}}]}; include every candidate and requested role.",prompt)
        decisions=[CandidateDecision(candidate_id=x["candidate_id"],classification=Classification.POSSIBLE,roles=x["roles"]) for x in data["decisions"]]
        return self._result(decisions,response,latency)
    async def _complete(self,instruction,value):
        body={"model":self.model,"messages":[{"role":"system","content":instruction},{"role":"user","content":json.dumps(value,separators=(",",":"))}],"temperature":0,"response_format":{"type":"json_object"}}
        if self.reasoning_effort: body["reasoning_effort"]=self.reasoning_effort
        if not self.structured_output: body.pop("response_format")
        started=time.perf_counter(); response=await self._post(body)
        try:
            content=response["choices"][0]["message"]["content"]
            data=json.loads(content) if isinstance(content,str) else content
        except (KeyError,IndexError,TypeError,ValueError) as exc: raise ProviderError(f"provider {self.name} returned invalid structured output") from exc
        return data,response,(time.perf_counter()-started)*1000
    async def _post(self,body):
        retrying=AsyncRetrying(stop=stop_after_attempt(self.max_retries+1),wait=wait_random_exponential(multiplier=.5,max=5),retry=retry_if_exception_type(_Transient),reraise=True)
        try:
            async for attempt in retrying:
                with attempt:
                    try: response=await self.client.post(self.base_url+self.endpoint,headers=self._headers(),json=body)
                    except (httpx.TimeoutException,httpx.NetworkError) as exc: raise _Transient(str(exc)) from exc
                    if response.status_code in {408,429,500,502,503,504}: raise _Transient(f"HTTP {response.status_code}: {response.text[:500]}")
                    response.raise_for_status(); data=response.json()
                    if not isinstance(data,dict): raise ProviderError("provider returned non-object JSON")
                    return data
        except _Transient as exc: raise ProviderError(f"provider {self.name} failed after {self.max_retries+1} attempts: {exc}") from exc
    def _result(self,decisions,response,latency):
        usage=response.get("usage") or {}; inp=usage.get("prompt_tokens",usage.get("input_tokens")); out=usage.get("completion_tokens",usage.get("output_tokens")); cost=usage.get("cost")
        if cost is None and (self.input_rate is not None or self.output_rate is not None): cost=((inp or 0)*(self.input_rate or 0)+(out or 0)*(self.output_rate or 0))/1_000_000
        return ProviderBatchResult(decisions,response.get("model"),inp,out,cost,latency,response.get("id"),response)
    async def health(self): return True
    async def close(self):
        if self._owns_client: await self.client.aclose()
