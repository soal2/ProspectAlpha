"""Object storage provider boundary for uploaded documents and reports."""

from __future__ import annotations

import os
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class StoredObject:
    uri: str
    bucket: str
    key: str
    size_bytes: int


class ObjectStore(Protocol):
    def put_bytes(
        self,
        *,
        key: str,
        content: bytes,
        content_type: str,
    ) -> StoredObject:
        ...

    def get_bytes(self, *, key: str) -> bytes:
        ...


class LocalObjectStore:
    def __init__(self, root: Path | str, bucket: str = "prospect-alpha") -> None:
        self.root = Path(root)
        self.bucket = bucket

    def put_bytes(
        self,
        *,
        key: str,
        content: bytes,
        content_type: str,
    ) -> StoredObject:
        path = self.root / self.bucket / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return StoredObject(
            uri=f"local://{path}",
            bucket=self.bucket,
            key=key,
            size_bytes=len(content),
        )

    def get_bytes(self, *, key: str) -> bytes:
        path = self.root / self.bucket / key
        if not path.exists():
            raise KeyError(key)
        return path.read_bytes()


class MinioObjectStore:
    def __init__(
        self,
        *,
        endpoint: str,
        access_key: str,
        secret_key: str,
        bucket: str,
        secure: bool = False,
    ) -> None:
        from minio import Minio

        self.client = Minio(
            endpoint,
            access_key=access_key,
            secret_key=secret_key,
            secure=secure,
        )
        self.bucket = bucket

    def put_bytes(
        self,
        *,
        key: str,
        content: bytes,
        content_type: str,
    ) -> StoredObject:
        if not self.client.bucket_exists(self.bucket):
            self.client.make_bucket(self.bucket)
        self.client.put_object(
            self.bucket,
            key,
            BytesIO(content),
            length=len(content),
            content_type=content_type,
        )
        return StoredObject(
            uri=f"minio://{self.bucket}/{key}",
            bucket=self.bucket,
            key=key,
            size_bytes=len(content),
        )

    def get_bytes(self, *, key: str) -> bytes:
        from minio.error import S3Error

        try:
            response = self.client.get_object(self.bucket, key)
        except S3Error as exc:
            raise KeyError(key) from exc
        try:
            return response.read()
        finally:
            response.close()
            response.release_conn()


def get_default_object_store() -> ObjectStore:
    provider = os.getenv("OBJECT_STORE_PROVIDER", "local").lower()
    bucket = os.getenv("OBJECT_STORE_BUCKET", "prospect-alpha")
    if provider == "minio":
        return MinioObjectStore(
            endpoint=os.getenv("MINIO_ENDPOINT", "localhost:9000"),
            access_key=os.getenv("MINIO_ACCESS_KEY", "change-me"),
            secret_key=os.getenv("MINIO_SECRET_KEY", "change-me"),
            bucket=bucket,
            secure=os.getenv("MINIO_SECURE", "0") == "1",
        )
    root = os.getenv("OBJECT_STORE_LOCAL_ROOT") or os.getenv("DOCUMENT_STORAGE_DIR")
    if not root:
        root = Path(__file__).resolve().parents[3] / "data" / "raw" / "object-store"
    return LocalObjectStore(root=root, bucket=bucket)
