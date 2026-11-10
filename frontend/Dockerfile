FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
ARG LISTENFLOW_API_URL=http://api:8765
ENV LISTENFLOW_API_URL=$LISTENFLOW_API_URL
RUN npm run build

FROM node:22-alpine
WORKDIR /app
ENV NODE_ENV=production
COPY --from=build /app ./
EXPOSE 3210
CMD ["npx", "next", "start", "-H", "0.0.0.0", "-p", "3210"]
