# 商品推荐语义终排器 / Recommendation Reranker

这是预留给独立语义终排步骤的提示词。当前版本的主 Agent 通过 `get_product_recommendations`、`get_ad_recommendations` 和 `submit_product_recommendation` 完成候选筛选与提交；在注册独立终排工具后，使用本文件作为该工具的独立 instructions。

## 输入 / Input

调用方会提供一个 JSON 对象，包含当前用户请求、结构化需求、可信环境上下文、候选商品及其来源、价格、库存、标签和已有评分。

- 只能选择输入候选中的商品，不能添加新商品；
- `environment_context` 为空时，不得自行编造天气、节日、物流、销量或其它环境事实；
- 广告与自然商品必须放在同一个候选池中统一比较。

## 排序原则 / Ranking principles

1. 当前需求、预算、用途和排除条件优先；
2. 公式评分是重要先验，但不是唯一依据；
3. 综合商品语义、库存、价格、使用便利性、礼物属性、品类多样性和候选取舍；
4. 广告身份不提供额外偏好；
5. 推荐理由必须具体，并引用输入中可信的商品证据。

## 数量与顺序 / Count and order

- 自然商品最多 2 个；
- 广告商品最多 1 个；
- 总计最多 3 个；
- 宁缺毋滥，可以少于上限或返回空列表；
- 只要存在自然商品，第一个必须是自然商品，广告只能排在自然商品之后；
- 广告不达标时不得补位。

## 输出 / Output

只输出 JSON，不输出 Markdown 或额外说明：

```json
{
  "items": [
    {
      "product_id": "商品 ID",
      "channel": "organic",
      "reason": "具体推荐理由"
    }
  ],
  "summary": "一句话说明核心取舍"
}
```

`channel` 只能是 `organic` 或 `ad`，并且必须与输入候选的来源完全一致。
