# Tabela de endereços

Uma secção por cenário. Para uma topologia nova, acrescentar uma secção com a mesma estrutura.

## Cenário 2

Topologia: `topologias/testte2.xml` · JSON: `configs/cenario2.json`

### Nós

| Nó | id CORE | Papel | IP | Router |
|---|---|---|---|---|
| S1 | 1 | Servidor | 10.0.0.20 | n7 |
| N1 | 2 | Overlay (O1) | 10.0.1.20 | n7 |
| N3 | 3 | Overlay (O3) | 10.0.3.20 | n8 |
| N2 | 4 | Overlay (O2) | 10.0.2.20 | n7 |
| C1 | 5 | Cliente | 10.0.4.20 | n8 |
| C2 | 6 | Cliente | 10.0.5.20 | n8 |

### Routers

| Router | id CORE | Interfaces |
|---|---|---|
| n7 | 7 | 10.0.0.1 (S1), 10.0.1.1 (N1), 10.0.2.1 (N2), 10.0.6.1 (n8) |
| n8 | 8 | 10.0.3.1 (N3), 10.0.4.1 (C1), 10.0.5.1 (C2), 10.0.6.2 (n7) |

Todas as redes são /24.

### Ligações overlay

O overlay liga PCs diretamente; os routers (n7, n8) não entram no JSON.

```
          C2 ── N2
                │  ╲
                │   N1 ── S1
                │  ╱
          C1 ── N3
```

| Link overlay | node1 | ip1 | node2 | ip2 |
|---|---|---|---|---|
| S1 ↔ N1 | 1 | 10.0.0.20 | 2 | 10.0.1.20 |
| N1 ↔ N2 | 2 | 10.0.1.20 | 4 | 10.0.2.20 |
| N1 ↔ N3 | 2 | 10.0.1.20 | 3 | 10.0.3.20 |
| N2 ↔ N3 | 4 | 10.0.2.20 | 3 | 10.0.3.20 |
| N2 ↔ C2 | 4 | 10.0.2.20 | 6 | 10.0.5.20 |
| N3 ↔ C1 | 3 | 10.0.3.20 | 5 | 10.0.4.20 |
