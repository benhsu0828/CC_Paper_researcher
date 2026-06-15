# with-code

結合 GitHub 開原始碼進行講解

## 使用方式

- 克隆或瀏覽論文的 GitHub 倉庫
- 找到核心實現程式碼
- 貼出關鍵程式碼片段並講解
- 將論文概念與程式碼實現對應

## 示例

```markdown
論文中的門控機制在程式碼中是這樣實現的：

```python
# engram/model.py
def compute_gate(self, hidden_state, memory_key):
    # 計算隱藏狀態和記憶 key 的對齊分數
    score = torch.matmul(hidden_state, memory_key.T)
    gate = torch.sigmoid(score / self.temperature)
    return gate
```

可以看到，門控值就是隱藏狀態和記憶 key 的點積，
經過 sigmoid 歸一化到 0-1 之間。
```

## 適用場景

想要復現或深入理解實現細節的讀者
