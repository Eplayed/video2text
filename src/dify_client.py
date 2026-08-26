"""Dify 知识库 API 客户端。

对接 Dify Dataset API（Knowledge Base）：
  - 列出/创建知识库
  - 通过文本创建/更新文档（每条视频一个文档，便于增量同步与溯源）
  - 删除文档

认证方式：Knowledge API Key（Dify 知识库页面左侧「API」处生成），Bearer 头。
"""

from __future__ import annotations

import requests
from typing import Any


class DifyKBError(Exception):
    """Dify API 调用失败（携带可直接展示的错误信息）。"""


class DifyKBClient:
    def __init__(self, api_base: str, api_key: str, timeout: int = 60):
        self.api_base = (api_base or "").rstrip("/")
        if not self.api_base:
            raise DifyKBError("Dify API Base 未配置")
        if not api_key:
            raise DifyKBError("Dify Knowledge API Key 未配置")
        # 统一以 /v1 结尾（Dify 知识库 API 前缀）
        if not self.api_base.endswith("/v1"):
            self.api_base += "/v1"
        self.api_key = api_key
        self.timeout = timeout

    def _request(self, method: str, path: str, **kwargs) -> Any:
        url = f"{self.api_base}{path}"
        headers = {"Authorization": f"Bearer {self.api_key}"}
        try:
            resp = requests.request(
                method, url, headers=headers, timeout=self.timeout, **kwargs
            )
        except requests.exceptions.Timeout:
            raise DifyKBError(f"请求超时（{self.timeout}s）：{url}")
        except requests.exceptions.ConnectionError as e:
            raise DifyKBError(f"无法连接 {self.api_base}：{e}")
        if resp.status_code == 401:
            raise DifyKBError("API Key 无效或已过期（HTTP 401），请到 Dify 知识库 API 页面重新生成")
        if resp.status_code == 404:
            raise DifyKBError("接口返回 404：请检查 API Base 地址是否为 Dify 服务地址")
        if resp.status_code >= 400:
            detail = ""
            try:
                detail = resp.json().get("message") or resp.text[:200]
            except Exception:
                detail = resp.text[:200]
            raise DifyKBError(f"HTTP {resp.status_code}: {detail}")
        if resp.status_code == 204 or not resp.text:
            return {}
        return resp.json()

    # ── 知识库 ──
    def list_datasets(self, limit: int = 100) -> list[dict[str, Any]]:
        """列出知识库（自动翻页，最多取 limit 条）。"""
        datasets: list[dict[str, Any]] = []
        page = 1
        while len(datasets) < limit:
            data = self._request("GET", "/datasets", params={"page": page, "limit": 30})
            batch = data.get("data") or []
            datasets.extend(batch)
            if len(batch) < 30 or not batch:
                break
            page += 1
        return datasets[:limit]

    def create_dataset(self, name: str) -> dict[str, Any]:
        return self._request("POST", "/datasets", json={"name": name})

    # ── 文档 ──
    def create_document_by_text(
        self,
        dataset_id: str,
        name: str,
        text: str,
    ) -> dict[str, Any]:
        """通过文本创建文档，高质量索引 + 通用分段。"""
        if not dataset_id:
            raise DifyKBError("未选择知识库（dataset_id 为空），请先在 AI 设置中选择或创建")
        payload = {
            "indexing_technique": "high_quality",
            "process_rule": {"mode": "automatic"},
            "doc_form": "text_model",
            "name": name,
            "text": text,
        }
        return self._request(
            "POST", f"/datasets/{dataset_id}/document/create-by-text", json=payload
        )

    def update_document_by_text(
        self,
        dataset_id: str,
        document_id: str,
        name: str,
        text: str,
    ) -> dict[str, Any]:
        payload = {
            "indexing_technique": "high_quality",
            "process_rule": {"mode": "automatic"},
            "doc_form": "text_model",
            "name": name,
            "text": text,
        }
        return self._request(
            "POST",
            f"/datasets/{dataset_id}/documents/{document_id}/update-by-text",
            json=payload,
        )

    def delete_document(self, dataset_id: str, document_id: str) -> None:
        self._request("DELETE", f"/datasets/{dataset_id}/documents/{document_id}")

    def test_connection(self) -> dict[str, Any]:
        """连通性测试：列一下知识库即可。"""
        datasets = self.list_datasets(limit=5)
        return {"ok": True, "dataset_count_sampled": len(datasets)}
