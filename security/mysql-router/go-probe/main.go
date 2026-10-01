// Matches Michelangelo v0.11.0's MySQL DSN and driver version, with no TLS
// parameters or cleartext-password overrides. The Router handles remote TLS.
package main

import (
	"context"
	"database/sql"
	"fmt"
	"os"
	"time"

	_ "github.com/go-sql-driver/mysql"
)

func main() {
	dsn := fmt.Sprintf("%s:%s@tcp(%s:%s)/%s?parseTime=true&loc=UTC", "transport", os.Getenv("MYSQL_PWD"), "127.0.0.1", "6446", "mysql")
	db, err := sql.Open("mysql", dsn)
	if err != nil {
		panic(err)
	}
	defer db.Close()
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	if err := db.PingContext(ctx); err != nil {
		panic(err)
	}
	var name, cipher string
	if err := db.QueryRowContext(ctx, "SHOW SESSION STATUS LIKE 'Ssl_cipher'").Scan(&name, &cipher); err != nil {
		panic(err)
	}
	if cipher == "" {
		panic("remote connection has no TLS cipher")
	}
	fmt.Printf("upstream DSN cold login successful; backend %s=%s\n", name, cipher)
}
