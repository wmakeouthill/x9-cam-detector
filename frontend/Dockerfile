# Estágio 1: Build da SPA
FROM node:22-alpine AS build

WORKDIR /app

# Fixa no pnpm 10 estável (a mesma versão do seu ambiente, sem os bloqueios do pnpm 12)
RUN corepack enable && corepack prepare pnpm@10.30.1 --activate

COPY package.json pnpm-lock.yaml* ./
RUN pnpm install --frozen-lockfile

COPY . .
RUN pnpm run build

# Estágio 2: Servidor de Produção Nginx Leve
FROM nginx:alpine

COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
