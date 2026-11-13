FROM golang:1.23-alpine AS build
WORKDIR /src
COPY go.mod main.go ./
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /server .

FROM gcr.io/distroless/static:nonroot
COPY --from=build /server /server
EXPOSE 8080
ENTRYPOINT ["/server"]
